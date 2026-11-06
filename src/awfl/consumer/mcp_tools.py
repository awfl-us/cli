"""MCP requests run inside the ordinary project-wide AWFL consumer.

The standard project event stream delivers tool calls and preserves the AWFL
session ID. Claims/results use normal Firebase authentication. A Firestore
transaction on the server claims a request before execution, so reconnects
cannot replay a command with side effects.
"""
import asyncio
import json
import os

import aiohttp

from awfl.auth import get_auth_headers
from awfl.utils import get_api_origin
from awfl.execution.runtime import Runtime

NAMES = {
    "RUN_COMMAND": "run_command",
    "READ_FILE": "read_file",
    "UPDATE_FILE": "write_file",
    "COMMAND_OUTPUT": "command_output",
    "COMMAND_CANCEL": "command_cancel",
    "ENVIRONMENT_STATUS": "environment_status",
}


class MCPRequestHandler:
    def __init__(self):
        self.runtime = Runtime(os.getenv("AWFL_EXECUTION_WORKSPACE", "/workspace"))
        self.tasks = set()

    def dispatch(self, event):
        # No second consumer, stream or GKE Job. All sessions share this runtime.
        task = asyncio.create_task(self.handle(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def handle(self, event):
        if not isinstance(event, dict) or event.get("source") != "mcp":
            return
        data = event.get("data") or {}
        meta = data.get("mcp") or {}
        tool = (data.get("tool_call") or {}).get("function") or {}
        workspace_id = meta.get("workspace_id")
        request_id = meta.get("request_id")
        command = NAMES.get(tool.get("name"))
        if not command or not isinstance(workspace_id, str) or len(workspace_id) != 32:
            return
        if not isinstance(request_id, int) or not 1 <= request_id <= 2000:
            return

        base = get_api_origin().rstrip("/")
        path = "/workflows/mcp-tools/{}/{}".format(workspace_id, request_id)
        headers = {"Content-Type": "application/json"}
        try:
            headers.update(get_auth_headers())
        except Exception:
            return

        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(base + path + "/claim", headers=headers, json={}) as response:
                    if response.status != 200:
                        return
                    claimed = (await response.json(content_type=None)).get("claimed")
                if not claimed:
                    return
                try:
                    args = tool.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args)
                    if not isinstance(args, dict):
                        raise ValueError("Invalid MCP tool arguments")
                    result = await self.runtime.invoke(command, args)
                except Exception as e:
                    result = {"error": type(e).__name__ + ": " + str(e)[:200]}
                # Fail closed: never re-execute claimed commands if delivery fails.
                # A pending request remains available for manual inspection.
                async with http.post(base + path + "/result", headers=headers, json=result) as response:
                    response.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return
