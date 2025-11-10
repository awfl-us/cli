import json
from typing import Optional, Dict, Any

from awfl.utils import log_unique
from .session_state import _update_status
from .rh_utils import ts_to_ms, is_background_from_payload


# Avoid noisy/duplicate cost lines
_last_cost_logged: Optional[float] = None


def apply_status(status: Optional[str], error: Optional[str], *, is_background: bool, ts_ms: int) -> None:
    """Update the prompt/status line. Non-logging side-effect via session state only."""
    _update_status(status, error, is_background=is_background, ts_ms=ts_ms)


def log_cost_if_changed(cost: Any) -> None:
    """Log a cost line when positive and changed from last."""
    global _last_cost_logged
    try:
        cost_val = float(cost or 0)
    except Exception:
        cost_val = 0
    if cost_val > 0 and cost_val != _last_cost_logged:
        log_unique(f"💰 ${cost_val}")
        _last_cost_logged = cost_val


def log_error_if_present(error: Any, *, is_background: bool) -> None:
    """Log an error line when present in the payload.

    We always log errors to make failures visible, even for background events.
    """
    if not error:
        return
    try:
        if isinstance(error, (dict, list)):
            err_text = json.dumps(error)
        else:
            err_text = str(error)
    except Exception:
        err_text = str(error)
    log_unique(f"❌ Error: {err_text}")


def log_user_message(user_msg: Optional[str], *, error: Optional[str], is_background: bool, ts_ms: int) -> None:
    """Handle non-error user-facing message logging and status hints embedded in content.

    - Suppresses logging for background messages
    - Parses "Workflow status:" hints to update status instead of logging
    - Suppresses noisy zero-cost lines like "💰 $0"
    - Logs regular content lines prefixed with a speaking emoji
    """
    if user_msg is None:
        return
    if isinstance(user_msg, str) and user_msg.strip().lower() == "null":
        return
    if is_background:
        return

    lower = user_msg.lower()
    if "workflow status:" in lower:
        if "done" in lower:
            _update_status("Done", None, is_background=False, ts_ms=ts_ms)
        elif "failed" in lower:
            _update_status("Failed", error, is_background=False, ts_ms=ts_ms)
        elif "running" in lower:
            _update_status("Running", None, is_background=False, ts_ms=ts_ms)
        return

    if user_msg.strip().startswith("💰 $0"):
        return

    log_unique(f"🗣️ {user_msg}")


def log_tool_call(name: str, args: Dict[str, Any], *, is_background: bool) -> None:
    """Log a concise tool-call message unless background."""
    if is_background:
        return
    msg = f"🔧 Tool call: {name}"
    upper = (name or "").upper()
    if upper in ("UPDATE_FILE", "READ_FILE"):
        fp = args.get("filepath")
        if fp:
            msg += f" -> {fp}"
    elif upper == "RUN_COMMAND":
        cmd = args.get("command")
        if cmd:
            cmd_short = cmd if len(cmd) <= 120 else cmd[:117] + "..."
            msg += f" -> {cmd_short}"
    log_unique(msg)


def log_run_command_sanitized(command: str, reason: Optional[str]) -> None:
    if not reason:
        return
    short_cmd = command if len(command) <= 160 else command[:157] + "..."
    log_unique(f"[RUN_COMMAND] sanitized ({reason}): {short_cmd}")


def process_event(data: Dict[str, Any]) -> None:
    """Aggregate all non-error status updates and logging for a response event.

    This is intentionally side-effect free with respect to tool execution: it only updates
    the prompt status and writes logs. Callers can choose to invoke this, the handler, or both.
    """
    # Timestamps and mode
    updated_at = data.get("create_time")
    ts_ms = ts_to_ms(updated_at) if updated_at else 0
    is_background = is_background_from_payload(data)

    # Status update first
    apply_status(data.get("status"), data.get("error"), is_background=is_background, ts_ms=ts_ms)

    # Error line (if present)
    if "error" in data and data.get("error"):
        log_error_if_present(data.get("error"), is_background=is_background)

    # Cost line (non-error)
    if "cost" in data:
        log_cost_if_changed(data.get("cost"))

    # User-facing content
    user_msg = data.get("content")
    if isinstance(user_msg, str) and user_msg.strip().lower() == "null":
        user_msg = None
    log_user_message(user_msg, error=data.get("error"), is_background=is_background, ts_ms=ts_ms)

    # Tool call logging (concise)
    tc = data.get("tool_call")
    if tc:
        fn = (tc or {}).get("function", {}) or {}
        name = (fn.get("name") or "").upper()
        args_raw = fn.get("arguments") or "{}"
        args: Dict[str, Any]
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except Exception:
            # Do not emit error logs from logger; fallback silently
            args = {}
        log_tool_call(name, args, is_background=is_background)
