"""MCP enhancements handled by the existing project SSE consumer.

No additional listener, Kubernetes Job or execution service is started. The
legacy AWFL tool callback remains the transport, with optional output events.
"""
import asyncio
import os
from pathlib import Path

from awfl.execution.runtime import Runtime
from .callbacks import post_internal_callback
from .output_batch import OutputBatchSink

NATIVE_NAMES = {
    "RUN_COMMAND": "run_command",
    "COMMAND_OUTPUT": "command_output",
    "COMMAND_CANCEL": "command_cancel",
    "COMMAND_INPUT": "command_input",
    "READ_FILE": "read_file",
    "UPDATE_FILE": "write_file",
    "APPLY_PATCH": "apply_patch",
    "SEARCH_FILES": "search_files",
    "LIST_DIRECTORY": "list_directory",
}

# Unlike the MCP request path, ordinary agent tool callbacks are already
# authorized by their session and have no MCP claim record. They can still use
# the same guarded filesystem/process runtime without another execution backend.
AGENT_NATIVE_NAMES = {
    "APPLY_PATCH", "SEARCH_FILES", "LIST_DIRECTORY",
    "COMMAND_OUTPUT", "COMMAND_INPUT", "COMMAND_CANCEL",
}
_ADVANCED_FILE_ARGS = {
    "READ_FILE": {"path", "offset", "max_bytes", "start_line", "end_line",
                  "include_line_numbers", "expected_hash", "encoding", "with_metadata"},
    "UPDATE_FILE": {"path", "create_only", "expected_hash", "create_parents",
                    "encoding", "permissions"},
    "RUN_COMMAND": {"cwd", "timeout_ms", "timeout", "yield_time_ms", "max_output_bytes",
                    "interactive", "pty", "rows", "cols", "session_id"},
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


def is_agent_native(name, args):
    """Keep historical tool-call behavior unless an agent opts into new options."""
    return name in AGENT_NATIVE_NAMES or bool(
        set(args) & _ADVANCED_FILE_ARGS.get(name, set()))


async def handle_agent_native(data, name, args, send_result, *, runtime=None):
    """Handle agent callbacks using the same implementation as MCP tools.

    No MCP claim/progress protocol is used here; those apply only to
    server-created MCP request records, not Workflow callback records.
    """
    command = NATIVE_NAMES[name]
    params = dict(args)
    if name in ("READ_FILE", "UPDATE_FILE") and "filepath" in params:
        params.setdefault("path", params.pop("filepath"))
    if name in ("RUN_COMMAND", "APPLY_PATCH") and "cwd" not in params:
        workdir = data.get("workdir")
        if workdir:
            params["cwd"] = workdir
    if name == "RUN_COMMAND" and "timeout" not in params and "timeout_ms" not in params:
        seconds = data.get("timeout_seconds")
        if seconds is not None:
            params["timeout"] = seconds
    if runtime is None:
        runtime = shared_runtime()
    try:
        result = await runtime.invoke(command, params)
        if name == "READ_FILE":
            # Historical Scala CliTools.readFile decodes the 'content' field.
            result.setdefault("content", result.get("text", ""))
            result.setdefault("filepath", args.get("filepath", args.get("path")))
        elif name == "UPDATE_FILE":
            result.setdefault("filepath", args.get("filepath", args.get("path")))
        elif name == "RUN_COMMAND":
            # Preserve the existing Scala output/error/exitCode contract while
            # also returning the MCP execution ID, state and chunk metadata.
            chunks = result.get("chunks") or []
            result["output"] = "".join(
                part.get("text", "") for part in chunks
                if part.get("stream") in ("stdout", "pty"))[:50000]
            result["error"] = "".join(
                part.get("text", "") for part in chunks
                if part.get("stream") == "stderr")[:50000]
            result["exitCode"] = result.get("exit_code")
    except Exception as exc:
        result = {"state": "failed", "error": _public_error(exc)}
    await send_result(result)
    return True


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
    sink = OutputBatchSink(callback_id, execution_id, post_internal_callback)

    try:
        result = await runtime.invoke("run_command", params, on_chunk=sink.append)
        item = runtime.executions[execution_id]
        await item["task"]
        # A completion callback may only be published once the bounded writer
        # has finished durably persisting all acknowledged output events.
        persisted = await sink.finish()
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
        # Never leave a background output writer orphaned after a shell error.
        # Preserve the original error and avoid falsely declaring the result
        # complete before pending output has been flushed.
        try:
            await sink.finish()
        except Exception:
            pass
        await send_result({"execution_id": execution_id, "state": "failed",
                           "exit_code": None, "error": _public_error(exc)})
    return True
