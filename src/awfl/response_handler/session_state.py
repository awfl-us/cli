import os
import time
from typing import Optional, Tuple

from awfl.state import get_active_workflow, normalize_workflow

# Global session and status state
_session_id: Optional[str] = None
_latest_status: str = ""
_latest_error: Optional[str] = None
_latest_status_ts_ms: int = 0  # monotonic guard


def set_session(new_session_id: Optional[str]) -> None:
    global _session_id, _latest_status, _latest_error, _latest_status_ts_ms
    # Reset status tracking when switching sessions/workflows
    if new_session_id != _session_id:
        _latest_status = ""
        _latest_error = None
        _latest_status_ts_ms = 0
    _session_id = new_session_id or ""


def get_session() -> str:
    """Resolve the current session identifier.
    Resolution order:
      1) ASSISTANT_WORKFLOW env (normalized)
      2) Selected active workflow (normalized)
      3) Locally tracked _session_id
    """
    env_wf = os.environ.get("ASSISTANT_WORKFLOW")
    if env_wf:
        return normalize_workflow(env_wf)

    wf = get_active_workflow()
    if wf:
        return normalize_workflow(wf)

    return _session_id or ""


def get_latest_status() -> Tuple[str, Optional[str]]:
    """Return the latest known (status, error) for the current session.
    Status is one of "", "Running", "Done", "Failed"; error is a short string when Failed.
    """
    return _latest_status, _latest_error


def set_prompt_status(status: str, error: Optional[str] = None, *, new_execution: bool = False) -> None:
    """Explicitly set the prompt status from the CLI.

    Use new_execution=True when starting a brand-new workflow execution to
    clear any prior terminal state. Typically not needed if the workflow
    immediately emits a fresh status (e.g., Running) with a newer timestamp.

    Note: If you prefer only workflow-driven statuses, do not call this.
    """
    global _latest_status, _latest_error, _latest_status_ts_ms
    if new_execution:
        # Reset monotonic guard to allow transition from terminal -> Running for a new exec
        _latest_status = ""
        _latest_error = None
        _latest_status_ts_ms = 0
    # Use current wall time as a monotonic-ish timestamp for local updates
    ts_ms = int(time.time() * 1000)
    _update_status(status, error, is_background=False, ts_ms=ts_ms)


def _update_status(status: str | None, error: Optional[str], *, is_background: bool, ts_ms: int) -> None:
    """Update in-memory latest status used by the prompt.
    Rules:
    - Ignore background messages.
    - Enforce monotonicity by message time (only accept >= last timestamp).
    - Accept newer statuses from the workflow as source-of-truth, including
      Running after Done/Failed if the timestamp is newer.
    - Track error text only when Failed.
    """
    global _latest_status, _latest_error, _latest_status_ts_ms
    status = (status or "").strip()
    if not status:
        return
    if is_background:
        return
    if ts_ms < _latest_status_ts_ms:
        return

    _latest_status_ts_ms = ts_ms
    _latest_status = status
    _latest_error = error if (status == "Failed" and error) else None
