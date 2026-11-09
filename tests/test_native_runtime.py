import asyncio
import hashlib
from pathlib import Path
import os
import resource
from unittest.mock import patch
import tempfile
import unittest

from awfl.execution.runtime import Runtime, child_limits


class NativeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = Runtime(self.root)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.tmp.cleanup()

    async def test_file_metadata_line_ranges_search_and_directory(self):
        (self.root / "src").mkdir()
        file = self.root / "src" / "main.py"
        file.write_text("alpha\nbeta\nalpha\n")
        content = await self.runtime.invoke("read_file", {
            "path": "src/main.py", "start_line": 2, "end_line": 3,
            "include_line_numbers": True,
        })
        self.assertEqual(content["text"], "2: beta\n3: alpha\n")
        self.assertEqual(content["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        matches = await self.runtime.invoke("search_files", {
            "pattern": "alpha", "mode": "content", "glob": "**/*.py",
            "respect_gitignore": False,
        })
        self.assertEqual([x["line"] for x in matches["matches"]], [1, 3])
        listing = await self.runtime.invoke("list_directory", {
            "path": "src", "recursive": True,
        })
        self.assertEqual(listing["entries"][0]["type"], "file")

    async def test_atomic_write_create_only_and_stale_hash(self):
        created = await self.runtime.invoke("write_file", {
            "path": "new/file.txt", "content": "one", "create_only": True,
        })
        self.assertTrue(created["created"])
        with self.assertRaises(ValueError):
            await self.runtime.invoke("write_file", {
                "path": "new/file.txt", "content": "two", "create_only": True,
            })
        with self.assertRaises(ValueError):
            await self.runtime.invoke("write_file", {
                "path": "new/file.txt", "content": "two", "expected_hash": "f" * 64,
            })
        await self.runtime.invoke("write_file", {
            "path": "new/file.txt", "content": "two", "expected_hash": created["sha256"],
        })
        self.assertEqual((self.root / "new/file.txt").read_text(), "two")

    async def test_patch_invocation(self):
        (self.root / "app.py").write_text("timeout = 5\n")
        response = await self.runtime.invoke("apply_patch", {
            "patch": "*** Begin Patch\n*** Update File: app.py\n@@\n-timeout = 5\n+timeout = 10\n*** End Patch",
        })
        self.assertTrue(response["atomic"])
        self.assertEqual((self.root / "app.py").read_text(), "timeout = 10\n")

    async def test_nonzero_exit_and_stable_cursor(self):
        output = []
        async def on_chunk(event):
            output.append(event)
        result = await self.runtime.invoke("run_command", {
            "command": "echo hello; echo problem >&2; exit 7",
            "execution_id": "exec_stable", "yield_time_ms": 500,
        }, on_chunk=on_chunk)
        await self.runtime.executions["exec_stable"]["task"]
        first = self.runtime.output("exec_stable", 0)
        self.assertEqual(first["state"], "completed")
        self.assertEqual(first["exit_code"], 7)
        self.assertIsNone(first["error"])
        self.assertTrue(len(output) >= 2)
        self.assertEqual(first["cursor"], len(output))
        self.assertEqual(self.runtime.output("exec_stable", first["cursor"])["chunks"], [])

    async def test_interactive_stdin_eof_and_pty(self):
        result = await self.runtime.invoke("run_command", {
            "command": "read line; printf 'got:%s\\n' \"$line\"",
            "interactive": True, "yield_time_ms": 10,
        })
        execution_id = result["execution_id"]
        await self.runtime.invoke("command_input", {
            "execution_id": execution_id, "input": "answer\n", "eof": True,
        })
        await self.runtime.executions[execution_id]["task"]
        self.assertIn("got:answer", self.runtime.output(execution_id, 0)["stdout"])
        other = await self.runtime.invoke("run_command", {
            "command": "echo from-pty", "pty": True, "yield_time_ms": 500,
        })
        await self.runtime.executions[other["execution_id"]]["task"]
        events = self.runtime.output(other["execution_id"], 0)["chunks"]
        self.assertTrue(any(x["stream"] == "pty" and "from-pty" in x["text"] for x in events))


    async def test_ten_megabytes_of_output_paginate_without_duplication(self):
        result = await self.runtime.invoke("run_command", {
            "command": "python -c \"import sys; sys.stdout.write('x'*10485760)\"",
            "yield_time_ms": 0,
        })
        eid = result["execution_id"]
        await self.runtime.executions[eid]["task"]
        cursor = 0
        total = 0
        seen = set()
        while True:
            page = self.runtime.output(eid, cursor, max_bytes=65536)
            for chunk in page["chunks"]:
                self.assertNotIn(chunk["cursor"], seen)
                seen.add(chunk["cursor"])
                total += len(chunk["text"].encode("utf-8"))
            if not page["has_more"]:
                break
            self.assertGreater(page["cursor"], cursor)
            cursor = page["cursor"]
        self.assertEqual(total, 10485760)
        self.assertFalse(page["truncated"])
        self.assertEqual(page["state"], "completed")

    async def test_cwd_is_per_command_not_global(self):
        (self.root / "nested").mkdir()
        one, two = await asyncio.gather(
            self.runtime.invoke("run_command", {"command": "pwd", "cwd": "nested"}),
            self.runtime.invoke("run_command", {"command": "pwd"}),
        )
        await asyncio.gather(self.runtime.executions[one["execution_id"]]["task"],
                             self.runtime.executions[two["execution_id"]]["task"])
        self.assertIn(str(self.root / "nested"), self.runtime.output(one["execution_id"], 0)["stdout"])
        self.assertEqual(self.runtime.output(two["execution_id"], 0)["stdout"].strip(), str(self.root))


if __name__ == "__main__":
    unittest.main()

class BrowserResourceLimitsTests(unittest.TestCase):
    def test_headless_browser_gets_bounded_process_thread_budget(self):
        with patch.dict(os.environ, {"AWFL_EXEC_SUBPROCESS_MAX_TASKS": "2048"}):
            with patch("awfl.execution.runtime.resource.setrlimit") as setter:
                child_limits()
        setter.assert_any_call(resource.RLIMIT_NPROC, (2048, 2048))
        setter.assert_any_call(resource.RLIMIT_NOFILE, (4096, 4096))

    def test_resource_limit_env_is_clamped_and_invalid_input_is_safe(self):
        for value, expected in (("bad", 2048), ("10", 512), ("999999", 4096)):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"AWFL_EXEC_SUBPROCESS_MAX_TASKS": value}):
                    with patch("awfl.execution.runtime.resource.setrlimit") as setter:
                        child_limits()
                setter.assert_any_call(resource.RLIMIT_NPROC, (expected, expected))
