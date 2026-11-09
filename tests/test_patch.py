import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from awfl.execution.patch import PatchConflict, apply_patch


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_multi_file_update_create_move_delete(self):
        a = self.root / "src" / "a.py"
        b = self.root / "src" / "b.py"
        d = self.root / "src" / "delete.txt"
        a.write_bytes(b"header\r\nvalue = 5\r\nfooter\r\n")
        b.write_text("retries = 2\n")
        d.write_text("obsolete\n")
        a.chmod(0o755)
        result = apply_patch(self.root, """*** Begin Patch
*** Update File: src/a.py
@@
 header
-value = 5
+value = 10
 footer
*** Update File: src/b.py
*** Move to: src/config.py
@@
-retries = 2
+retries = 3
*** Add File: src/new.py
+print("hello")
*** Delete File: src/delete.txt
*** End Patch""")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["files"]), 4)
        self.assertEqual(a.read_bytes(), b"header\r\nvalue = 10\r\nfooter\r\n")
        self.assertEqual(a.stat().st_mode & 0o777, 0o755)
        self.assertFalse(b.exists())
        self.assertEqual((self.root / "src/config.py").read_text(), "retries = 3\n")
        self.assertEqual((self.root / "src/new.py").read_text(), 'print("hello")\n')
        self.assertFalse(d.exists())

    def test_invalid_context_cannot_partially_apply(self):
        a = self.root / "src/a.txt"
        b = self.root / "src/b.txt"
        a.write_text("ok\n")
        b.write_text("different\n")
        with self.assertRaises(PatchConflict):
            apply_patch(self.root, """*** Begin Patch
*** Update File: src/a.txt
@@
-ok
+updated
*** Update File: src/b.txt
@@
-missing
+new
*** End Patch""")
        self.assertEqual(a.read_text(), "ok\n")
        self.assertEqual(b.read_text(), "different\n")

    def test_stale_hash_rejected(self):
        a = self.root / "src/a.txt"
        a.write_text("current\n")
        with self.assertRaisesRegex(PatchConflict, "Stale content hash"):
            apply_patch(self.root, """*** Begin Patch
*** Update File: src/a.txt
@@
-current
+changed
*** End Patch""", expected_hashes={"src/a.txt": hashlib.sha256(b"old").hexdigest()})
        self.assertEqual(a.read_text(), "current\n")

    def test_ambiguous_context_rejected(self):
        a = self.root / "src/a.txt"
        a.write_text("same\nsame\n")
        with self.assertRaisesRegex(PatchConflict, "not unique"):
            apply_patch(self.root, """*** Begin Patch
*** Update File: src/a.txt
@@
-same
+changed
*** End Patch""")
        self.assertEqual(a.read_text(), "same\nsame\n")

    def test_symlink_and_traversal_rejected(self):
        external = self.root.parent / "external_patch_guard.txt"
        external.write_text("guard\n")
        try:
            (self.root / "src/link.txt").symlink_to(external)
            for file in ["src/link.txt", "../external_patch_guard.txt"]:
                with self.assertRaises(PatchConflict):
                    apply_patch(self.root, "*** Begin Patch\n*** Update File: " + file +
                                "\n@@\n-guard\n+bad\n*** End Patch")
            self.assertEqual(external.read_text(), "guard\n")
        finally:
            external.unlink()

    def test_expected_hash_success(self):
        a = self.root / "src/a.txt"
        a.write_text("correct\n")
        hash_ = hashlib.sha256(a.read_bytes()).hexdigest()
        result = apply_patch(self.root, """*** Begin Patch
*** Update File: src/a.txt
@@
-correct
+updated
*** End Patch""", expected_hashes={"src/a.txt": hash_})
        self.assertEqual(result["files"][0]["sha256"], hashlib.sha256(b"updated\n").hexdigest())


if __name__ == "__main__":
    unittest.main()
