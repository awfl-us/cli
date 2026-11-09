import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from awfl.consumer.workspace_sync import WorkspaceSync, allowed_file, _safe_target


class MemoryBucket(WorkspaceSync):
    def __init__(self, root):
        super().__init__(root, origin="https://example.invalid", user_id="owner",
                         project_id="project", consumer_id="worker", capability="cap")
        self.objects = {}
        self.counter = 0

    async def _broker(self, session):
        self.bucket = "test-bucket"
        self.prefix = "users/owner/projects/project/files/"
        return "scoped-token"

    async def _list(self, session, token):
        return {k: {"generation": v[0], "size": len(v[1]), "mode": v[2]} for k, v in self.objects.items()}

    async def _download(self, session, token, rel, generation):
        value = self.objects[rel]
        assert value[0] == generation
        return value[1]

    async def _upload(self, session, token, rel, content, generation, mode=0o644):
        prev = self.objects.get(rel)
        if prev and prev[0] != generation:
            raise ValueError("Precondition Failed: existing generation")
        if not prev and generation is not None:
            raise ValueError("Precondition Failed: object vanished")
        self.counter += 1
        result = str(100 + self.counter)
        self.objects[rel] = (result, content, mode)
        return result

    async def _delete(self, session, token, rel, generation):
        if self.objects[rel][0] != generation:
            raise ValueError("Precondition Failed: object changed")
        del self.objects[rel]


class WorkspaceSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bucket = MemoryBucket(self.root)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_restore_modify_and_restart(self):
        self.bucket.objects["repo/src/readme.md"] = ("17", b"original", 0o644)
        self.assertEqual(await self.bucket.sync_once(), 1)
        target = self.root / "repo/src/readme.md"
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(await self.bucket.sync_once(), 0)

        target.write_bytes(b"updated from cli")
        self.assertEqual(await self.bucket.sync_once(), 1)
        self.assertEqual(self.bucket.objects["repo/src/readme.md"][1], b"updated from cli")

        with tempfile.TemporaryDirectory() as cold_root:
            cold = MemoryBucket(Path(cold_root))
            cold.objects = dict(self.bucket.objects)
            self.assertEqual(await cold.sync_once(), 1)
            self.assertEqual((Path(cold_root) / "repo/src/readme.md").read_bytes(), b"updated from cli")

    async def test_local_delete_propagates_by_generation(self):
        self.bucket.objects["remove.txt"] = ("12", b"remove this", 0o644)
        await self.bucket.sync_once()
        (self.root / "remove.txt").unlink()
        await self.bucket.sync_once()
        self.assertNotIn("remove.txt", self.bucket.objects)

    async def test_remote_delete_propagates_when_local_unchanged(self):
        self.bucket.objects["remove.txt"] = ("12", b"remote will delete", 0o644)
        await self.bucket.sync_once()
        del self.bucket.objects["remove.txt"]
        await self.bucket.sync_once()
        self.assertFalse((self.root / "remove.txt").exists())

    async def test_parallel_changes_do_not_overwrite(self):
        self.bucket.objects["conflict.txt"] = ("12", b"initial", 0o644)
        await self.bucket.sync_once()
        (self.root / "conflict.txt").write_bytes(b"local update")
        self.bucket.objects["conflict.txt"] = ("13", b"remote update", 0o644)
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            await self.bucket.sync_once()
        self.assertEqual((self.root / "conflict.txt").read_bytes(), b"local update")
        self.assertEqual(self.bucket.objects["conflict.txt"][1], b"remote update")

    async def test_cold_boot_never_clobbers_preexisting_local_file(self):
        (self.root / "collision.txt").write_bytes(b"local content")
        self.bucket.objects["collision.txt"] = ("12", b"remote content", 0o644)
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            await self.bucket.sync_once()
        self.assertEqual((self.root / "collision.txt").read_bytes(), b"local content")

    async def test_executable_bit_survives_restore_and_chmod_upload(self):
        self.bucket.objects["repo/run.sh"] = ("12", b"#!/bin/sh\necho hi\n", 0o755)
        await self.bucket.sync_once()
        file = self.root / "repo/run.sh"
        self.assertEqual(file.stat().st_mode & 0o777, 0o755)
        file.chmod(0o700)
        await self.bucket.sync_once()
        self.assertEqual(self.bucket.objects["repo/run.sh"][2], 0o700)

    async def test_oversized_local_file_is_not_treated_as_deletion(self):
        from awfl.consumer import workspace_sync
        self.bucket.objects["large.txt"] = ("12", b"was small", 0o644)
        await self.bucket.sync_once()
        (self.root / "large.txt").write_bytes(b"oversize content")
        with patch.object(workspace_sync, "MAX_FILE_BYTES", 10):
            with self.assertRaisesRegex(RuntimeError, "conflict"):
                await self.bucket.sync_once()
        self.assertIn("large.txt", self.bucket.objects)

    async def test_sensitive_paths_and_traversal_excluded(self):
        for p in (".env", "app/.env.production", "foo/.ssh/private", "repo/node_modules/pkg.js",
                  "repo/.terraform/terraform.tfstate", "../../etc/passwd", "a//b", "/root/creds"):
            self.assertFalse(allowed_file(p), p)
        for p in ("repo/src/main.py", "repo/.git/HEAD", "repo/.git/objects/aa/0123"):
            self.assertTrue(allowed_file(p), p)
        (self.root / ".env").write_text("ACCESS_SECRET=should-not-upload")
        (self.root / "safe.py").write_text("print('safe')")
        await self.bucket.sync_once()
        self.assertEqual(set(self.bucket.objects), {"safe.py"})

        (self.root / "indirect").symlink_to(self.root / "elsewhere")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            _safe_target(self.root, "indirect/data.txt")

    async def test_cloud_only(self):
        with patch.dict(os.environ, {"AWFL_CONSUMER_TYPE": "LOCAL", "AWFL_WORKSPACE_GCS_BUCKET": "test"}):
            self.assertIsNone(WorkspaceSync.from_environment())


if __name__ == "__main__":
    unittest.main()
