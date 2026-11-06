import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock

from aiohttp import web

from awfl.consumer.mcp_tools import MCPRequestHandler
from awfl.consumer.sse_consumer import _forward_sse_event


class ProjectConsumerMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_event_type_dispatches_project_mcp_without_ordinary_execution(self):
        # Exactly the event format produced by the server: the envelope type is
        # an SSE header, while its JSON body contains only the event data.
        payload = {
            "mcp": {"workspace_id": "a" * 32, "request_id": 1},
            "tool_call": {"function": {"name": "RUN_COMMAND", "arguments": {"command": "echo ok"}}},
        }
        consumer = Mock()
        with patch("awfl.consumer.sse_consumer.forward_event", new_callable=AsyncMock) as normal:
            await _forward_sse_event(payload, "mcp_tool_call", scope="project", mcp_handler=consumer)
            consumer.dispatch.assert_called_once_with({
                "type": "mcp_tool_call", "source": "mcp", "data": payload,
            })
            normal.assert_not_awaited()  # Never run this command twice.

            consumer.reset_mock()
            await _forward_sse_event({"content": "completed"}, "mcp_tool_result",
                                     scope="project", mcp_handler=consumer)
            consumer.dispatch.assert_not_called()
            normal.assert_not_awaited()

            await _forward_sse_event(payload, "mcp_tool_call", scope="session", mcp_handler=None)
            normal.assert_awaited_once_with(payload, mode="log")
            normal.reset_mock()

            await _forward_sse_event({"tool_call": {"function": {"name": "RUN_COMMAND"}}},
                                     "message", scope="project", mcp_handler=consumer)
            normal.assert_awaited_once()
            self.assertEqual(normal.await_args.kwargs["mode"], "execute")

    async def test_mcp_request_uses_existing_project_auth_and_claims_once(self):
        claims = []
        results = []
        calls = []
        session = "normal-awfl-session"
        workspace = "a" * 32

        async def claim(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-user-token")
            self.assertEqual(request.headers["x-project-id"], "selected-project")
            claims.append(request.match_info["request"])
            return web.json_response({"claimed": len(claims) == 1})

        async def result(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-user-token")
            results.append(await request.json())
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_post("/workflows/mcp-tools/{workspace}/{request}/claim", claim)
        app.router.add_post("/workflows/mcp-tools/{workspace}/{request}/result", result)
        server = web.AppRunner(app)
        await server.setup()
        site = web.TCPSite(server, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        class FakeRuntime:
            async def invoke(self, name, args):
                calls.append((name, args))
                return {"execution_id": "command-123", "state": "running"}

        handler = MCPRequestHandler(runtime=FakeRuntime())
        event = {
            "type": "mcp_tool_call", "source": "mcp",
            "sessionId": session, "projectId": "selected-project",
            "data": {
                "mcp": {"workspace_id": workspace, "request_id": 1},
                "tool_call": {"function": {"name": "RUN_COMMAND", "arguments": {"command": "echo hello"}}},
            },
        }
        try:
            with patch("awfl.consumer.mcp_tools.get_api_origin", return_value=origin), patch(
                "awfl.consumer.mcp_tools.get_auth_headers",
                return_value={"Authorization": "Bearer test-user-token", "x-project-id": "selected-project"},
            ):
                await handler.handle(event)
                await handler.handle(event)  # SSE replay is not executed twice.
                await handler.handle({**event, "source": "another-client"})
            self.assertEqual(len(claims), 2)
            self.assertEqual(calls, [("run_command", {"command": "echo hello"})])
            self.assertEqual(results, [{"execution_id": "command-123", "state": "running"}])
        finally:
            await server.cleanup()

    async def test_default_runtime_root_uses_local_cwd_when_cloud_override_absent(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("awfl.consumer.mcp_tools.os.getcwd", return_value=root):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("AWFL_CONSUMER_WORKSPACE", None)
                    handler = MCPRequestHandler()
                    self.assertEqual(str(handler.runtime.root), root)

    async def test_session_viewer_does_not_execute_mcp_commands(self):
        handler = MCPRequestHandler(runtime=object())
        self.assertIsNone(await handler.handle({"source": "mcp", "data": {}}))
        self.assertIsNone(await handler.handle({"type": "normal_message", "data": {}}))
