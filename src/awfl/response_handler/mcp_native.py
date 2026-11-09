"""MCP enhancements handled by the existing project SSE consumer.

No additional listener, Kubernetes Job or execution service is started. The
legacy AWFL tool callback remains the transport, with optional output events.
"""
import asyncio
import os
from pathlib import Path

from awfl.execution.runtime import Runtime
from .callbacks import post_internal_callback

NATIVE_NAMES = {
    "RUN_COMMAND": "run_command",
    "COMMAND_CANCEL": "command_cancel",
    "COMMAND_INPUT": "command_input",
    "READ_FILE": "read_file",
    "UPDATE_FILE": "write_file",
    "APPLY_PATCH": "apply_patch",
    "SEARCH_FILES": "search_files",
    "LIST_DIRECTORY": "list_directory",
}

_RUNTIME = None


def shared_runtime():
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime(os.getenv("AWFL_CONSUMER_WORKSPACE") or os.getcwd())
    return _RUNTIME


def _public_error(exc):
    # Client-facing failures are intentionally bounded and do not reveal
    # tracebacks or arbitrary exception payloads.
    text = str(exc)[:240]
    if isinstance(exc, (ValueError, KeyError)):
        code = "file_conflict" if "hash" in text.lower() or "patch" in text.lower() or "exists" in text.lower() else "invalid_argument"
        return {"code": code, "message": text, "retryable": False}
    if isinstance(exc, PermissionError):
        return {"code": "permission_denied", "message": "Workspace permission denied", "retryable": False}
    return {"code": "execution_failed", "message": "Consumer tool operation failed", "retryable": False}


async def handle_native(data, name, args, send_result):
    """Execute a normal tool_call's MCP extension and respond on its callback."""
    command = NATIVE_NAMES.get(name)
    if not command:
        return False
    if command in ("read_file", "write_file") and "filepath" in args:
        args = {**args, "path": args["filepath"]}
    meta = data.get("mcp") or {}
    runtime = shared_runtime()
    callback_id = data.get("callback_id")
    # The server's existing request record is claimed transactionally BEFORE
    # any side effect. SSE reconnects cannot replay a completed command.
    if not callback_id or not await post_internal_callback(callback_id, {
        "_awfl_mcp_event": "claim",
        "consumer_id": os.environ.get("AWFL_CONSUMER_ID", ""),
        "pod_uid": os.environ.get("AWFL_POD_UID", ""),
    }):
        return True
    if command != "run_command":
        try:
            result = await runtime.invoke(command, args)
        except Exception as exc:
            result = {"state": "failed", "error": _public_error(exc)}
        await send_result(result)
        return True

    execution_id = meta.get("execution_id") or args.get("execution_id")
    if not execution_id:
        await send_result({"state": "failed", "error": {"code": "invalid_argument",
                          "message": "Missing execution ID", "retryable": False}})
        return True
    params = {**args, "execution_id": execution_id}
    send_lock = asyncio.Lock()
    persisted = set()

    async def persist(event):
        if not callback_id:
            return
        async with send_lock:
            ok = await post_internal_callback(callback_id, {
                "_awfl_mcp_event": "output",
                "execution_id": execution_id,
                "chunk": event,
            })
            if ok:
                persisted.add(event["cursor"])

    try:
        result = await runtime.invoke("run_command", params, on_chunk=persist)
        item = runtime.executions[execution_id]
        await item["task"]
        # Retry any transiently failed chunk deliveries before final callback.
        for event in item["chunks"]:
            if event["cursor"] not in persisted:
                await persist(event)
        finished = runtime.output(execution_id, len(item["chunks"]))
        await send_result({
            "execution_id": execution_id,
            "state": finished["state"], "exit_code": finished["exit_code"],
            "cwd": item["cwd"], "pty": item["pty"],
            "cursor": len(item["chunks"]), "has_more": False,
            "truncated": finished["truncated"], "reason": finished["reason"],
            "output_incomplete": len(persisted) != len(item["chunks"]),
            "error": None,
        })
    except Exception as exc:
        await send_result({"execution_id": execution_id, "state": "failed",
                           "exit_code": None, "error": _public_error(exc)})
    return True
