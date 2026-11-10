"""Small deterministic agent-tool contract tests; no cloud, LLM or sleeps."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from awfl.execution.runtime import Runtime
from awfl.response_handler.mcp_native import handle_agent_native, is_agent_native
from awfl.response_handler.handler import handle_response


class AgentToolParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root)
        self.replies = []

    async def _cleanup(self):
        self.temp.cleanup()

    async def call(self, name, args, data=None):
        async def reply(value):
            self.replies.append(value)
        await handle_agent_native(data or {}, name, args, reply, runtime=self.runtime)
        return self.replies[-1]

    async def test_patch_search_list_and_metadata_round_trip(self):
        patch_text = "*** Begin Patch\n*** Add File: hello.txt\n+title: AWFL tools\n*** End Patch"
        applied = await self.call("APPLY_PATCH", {"patch": patch_text})
        self.assertTrue(applied["ok"])
        self.assertEqual((self.root / "hello.txt").read_text().strip(), "title: AWFL tools")
        found = await self.call("SEARCH_FILES", {"pattern": "AWFL", "max_results": 3})
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["matches"][0]["line"], 1)
        listed = await self.call("LIST_DIRECTORY", {"path": "."})
        self.assertIn("hello.txt", [entry["name"] for entry in listed["entries"]])
        read = await self.call("READ_FILE", {
            "filepath": "hello.txt", "start_line": 1, "end_line": 1,
            "include_line_numbers": True, "with_metadata": True,
        })
        self.assertIn("title: AWFL tools", read["content"])
        self.assertEqual(len(read["sha256"]), 64)
        self.assertEqual(read["filepath"], "hello.txt")

    async def test_atomic_patch_conflict_never_modifies_existing_file(self):
        (self.root / "keep.txt").write_text("before\n")
        patch_text = ("*** Begin Patch\n"
                      "*** Update File: keep.txt\n@@\n-old content\n+new content\n"
                      "*** End Patch")
        result = await self.call("APPLY_PATCH", {"patch": patch_text})
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "file_conflict")
        self.assertEqual((self.root / "keep.txt").read_text(), "before\n")

    async def test_advanced_write_respects_create_only(self):
        result = await self.call("UPDATE_FILE", {
            "filepath": "once.txt", "content": "one", "create_only": True,
        })
        self.assertTrue(result["created"])
        self.assertEqual(result["filepath"], "once.txt")
        rejected = await self.call("UPDATE_FILE", {
            "filepath": "once.txt", "content": "two", "create_only": True,
        })
        self.assertEqual(rejected["state"], "failed")
        self.assertEqual((self.root / "once.txt").read_text(), "one")

    async def test_advanced_file_operations_respect_workflow_workdir(self):
        (self.root / "repo").mkdir()
        workdir = {"workdir": "repo"}
        write = await self.call("UPDATE_FILE", {
            "filepath": "nested/example.txt", "content": "workdir parity", "create_only": True,
        }, workdir)
        self.assertTrue(write["created"])
        self.assertEqual((self.root / "repo/nested/example.txt").read_text(), "workdir parity")
        read = await self.call("READ_FILE", {
            "filepath": "nested/example.txt", "start_line": 1, "end_line": 1,
        }, workdir)
        self.assertEqual(read["content"], "workdir parity")
        found = await self.call("SEARCH_FILES", {"pattern": "workdir parity"}, workdir)
        self.assertEqual(len(found["matches"]), 1)
        listed = await self.call("LIST_DIRECTORY", {"recursive": True}, workdir)
        self.assertTrue(any(entry["name"] == "example.txt" for entry in listed["entries"]))
        blocked = await self.call("READ_FILE", {
            "filepath": "../../outside", "start_line": 1,
        }, workdir)
        self.assertEqual(blocked["state"], "failed")

    async def test_async_shell_polls_existing_execution_and_preserves_old_field_aliases(self):
        started = await self.call("RUN_COMMAND", {
            "command": "printf agent-native", "yield_time_ms": 0,
        })
        execution_id = started["execution_id"]
        self.assertIsInstance(execution_id, str)
        await self.runtime.executions[execution_id]["task"]
        polled = await self.call("COMMAND_OUTPUT", {
            "execution_id": execution_id, "cursor": 0, "wait_ms": 1000,
        })
        self.assertEqual(polled["state"], "completed")
        self.assertIn("agent-native", str(polled.get("chunks")))

    async def test_execution_history_has_no_lifetime_cap_or_eviction(self):
        runtime = Runtime(self.root)
        for index in range(128):
            runtime.executions[f"old-{index}"] = {"state": "completed"}
        started = await runtime.invoke("run_command", {
            "command": "printf after-128", "yield_time_ms": 1000,
        })
        self.assertEqual(started["state"], "completed")
        self.assertEqual(len(runtime.executions), 129)
        self.assertIn("old-0", runtime.executions)
        self.assertIn("old-127", runtime.executions)

    async def test_normal_callback_routes_patch_and_legacy_tools_stay_unchanged(self):
        self.assertTrue(is_agent_native("APPLY_PATCH", {"patch": "x"}))
        self.assertFalse(is_agent_native("READ_FILE", {"filepath": "x"}))
        self.assertFalse(is_agent_native("RUN_COMMAND", {"command": "echo x"}))
        self.assertTrue(is_agent_native("RUN_COMMAND", {"command": "echo x", "yield_time_ms": 0}))
        callback = AsyncMock(return_value=True)
        event = {"callback_id": "test-callback", "tool_call": {"function": {
            "name": "APPLY_PATCH",
            "arguments": '{"patch":"*** Begin Patch\\n*** Add File: from-agent.txt\\n+ok\\n*** End Patch"}',
        }}}
        with patch("awfl.response_handler.mcp_native._RUNTIME", self.runtime), \
             patch("awfl.response_handler.handler.post_internal_callback", callback), \
             patch("awfl.response_handler.handler.get_session", return_value="test-session"):
            await handle_response(event)
        self.assertEqual((self.root / "from-agent.txt").read_text(), "ok\n")
        callback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
