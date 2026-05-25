import os
import json
import uuid
import subprocess
from pathlib import Path
from datetime import datetime

from awfl.utils import log_unique

from .callbacks import post_internal_callback
from .rh_utils import read_file_text_utf8_ignore, sanitize_shell_command
from .session_state import get_session


def _resolve_path_for_io(filepath: str | os.PathLike, workdir: str | os.PathLike | None) -> Path:
    """
    Map a requested filepath to the provided workdir if present.
    - Relative paths are interpreted relative to workdir.
    - Absolute paths are left unchanged.
    """
    p = Path(filepath)
    if not workdir:
        return p
    try:
        wd = Path(workdir)
    except Exception:
        return p
    if p.is_absolute():
        return p
    return wd / p


def _get_cwd_for_commands(workdir: str | os.PathLike | None) -> str:
    try:
        return str(Path(workdir)) if workdir else str(Path.cwd())
    except Exception:
        return str(Path.cwd())


async def handle_response(data: dict):
    # Internal callback by id is now required; direct callback URLs are no longer supported
    callback_id = data.get("callback_id")

    updated_at = data.get("create_time")

    # Current session
    session_id = get_session()

    # Optional per-event working directory (when provided by SSE event)
    workdir = data.get("workdir")
    if workdir:
        log_unique(f"workdir provided; routing IO and commands relative to: {workdir}")

    # Unified sender: POST via internal service; if callback_id missing, log and return
    async def send_result(payload: dict):
        if not callback_id:
            log_unique("No callback_id provided; skipping callback delivery")
            return
        cid = uuid.uuid4().hex[:8]
        await post_internal_callback(callback_id, payload, correlation_id=cid)

    # 1) Preferred path: tool_calls (tool-enabled chat)
    tc = data.get("tool_call")

    if tc:
        fn = (tc or {}).get("function", {}) or {}
        name = (fn.get("name") or "").upper()
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except Exception as e:
            # Keep error logging in handler
            log_unique(f"Bad arguments JSON for tool {name}: {args_raw!r}")
            await send_result({
                "error": f"Failed to parse tool arguments: {args_raw!r}\n{e}"
            })
            args = {}

        if name == "UPDATE_FILE":
            filepath = args.get("filepath")
            content = args.get("content")
            if filepath and content is not None:
                try:
                    path = _resolve_path_for_io(filepath, workdir)
                    if not path.parent.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                    await send_result({
                        "filepath": filepath,
                        "sessionId": session_id,
                        "timestamp": updated_at or datetime.utcnow().isoformat() + "Z",
                    })
                except Exception as e:
                    # Error logging remains local
                    log_unique(f"Failed to write file: {filepath} — {e}")

        elif name == "RUN_COMMAND":
            command = args.get("command")
            if command:
                # Best-effort sanitize common LLM artifacts (no non-error logging here)
                sanitized, _reason = sanitize_shell_command(command)
                command = sanitized

                # Determine effective timeout from event or env default
                _MISSING = object()
                ts_value = data.get("timeout_seconds", _MISSING)
                try:
                    env_default = int(os.environ.get("RUN_COMMAND_TIMEOUT_SECONDS", "120"))
                except Exception:
                    env_default = 120

                if ts_value is _MISSING:
                    timeout_sec = env_default
                    timeout_src = "env default"
                elif ts_value is None:
                    timeout_sec = None  # No timeout
                    timeout_src = "event null (no timeout)"
                else:
                    try:
                        timeout_sec = float(ts_value)
                        timeout_src = "event override"
                    except Exception:
                        timeout_sec = env_default
                        timeout_src = "env default (invalid event.timeout_seconds)"

                try:
                    result = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=timeout_sec,
                        cwd=_get_cwd_for_commands(workdir),
                    )
                    output_full = result.stdout or ""
                    error = (result.stderr or "").strip()
                    output = (
                        (output_full.strip()[:50000] + "...Output truncated")
                        if len(output_full) > 50000
                        else output_full.strip()
                    )
                    exit_code = result.returncode
                    await send_result({
                        "sessionId": session_id,
                        "command": command,
                        "output": output,
                        "error": error,
                        "exitCode": exit_code,
                        "timestamp": updated_at or datetime.utcnow().isoformat() + "Z",
                    })
                except subprocess.TimeoutExpired as e:
                    # Command exceeded the timeout; capture any partial output and report timeout
                    partial_out = (e.output or "") if isinstance(e.output, str) else (e.output.decode("utf-8", errors="ignore") if e.output else "")
                    output = (
                        (partial_out.strip()[:50000] + "...Output truncated")
                        if len(partial_out) > 50000
                        else partial_out.strip()
                    )
                    stderr_text = (e.stderr or "") if isinstance(e.stderr, str) else (e.stderr.decode("utf-8", errors="ignore") if e.stderr else "")
                    err_msg = f"Timed out after {timeout_sec}s" + (f": {stderr_text.strip()}" if stderr_text else "")
                    # Log timeout with explicit null exit code
                    log_unique("RUN_COMMAND timed out: exit=null")
                    await send_result({
                        "sessionId": session_id,
                        "command": command,
                        "output": output,
                        "error": err_msg,
                        "timestamp": updated_at or datetime.utcnow().isoformat() + "Z",
                        "timed_out": True,
                        "exitCode": None,
                    })
                except Exception as e:
                    # Error logging remains in handler
                    log_unique(f"Command failed: {e}")

        elif name == "READ_FILE":
            filepath = args.get("filepath")
            if filepath:
                try:
                    # Binary-safe read: decode as UTF-8 with replacement to avoid exceptions
                    mapped_path = _resolve_path_for_io(filepath, workdir)
                    content = read_file_text_utf8_ignore(str(mapped_path))
                    # Avoid sending overly large payloads back in callbacks
                    max_bytes = int(os.environ.get("READ_FILE_MAX_BYTES", "200000"))
                    content_to_send = content[:max_bytes]
                    truncated = len(content) > len(content_to_send)

                    await send_result({
                        "sessionId": session_id,
                        "filepath": filepath,
                        "content": content_to_send,
                        "truncated": truncated,
                        "timestamp": updated_at or datetime.utcnow().isoformat() + "Z",
                    })
                except Exception as e:
                    # Error logging remains in handler
                    log_unique(f"Failed to read file: {filepath} — {e}")
                    await send_result({
                        "sessionId": session_id,
                        "filepath": filepath,
                        "content": str(e),
                        "timestamp": updated_at or datetime.utcnow().isoformat() + "Z",
                    })

        else:
            # Unknown tool considered an error-ish condition; keep local logging
            log_unique(f"Unknown tool: {name}")

        # Done handling tool_calls; return
        return

    # No action provided: intentionally quiet
    return
