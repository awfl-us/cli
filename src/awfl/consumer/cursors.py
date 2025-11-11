import asyncio
from typing import Any, Dict, Optional

import aiohttp

from awfl.auth import get_auth_headers
from awfl.utils import get_api_origin, log_unique

# Back-compat note:
# The previous implementation persisted a local JSON file with a map of
#   { "projects": { "<projectId>:<sessionId>": { "last_event_id": str } } }
# We now use the remote /api/workflows/events/cursors service to fetch and
# update per-project and per-session cursors so multiple consumers can run in
# parallel without interfering. The helper functions below are asynchronous and
# operate against the remote API using the provided aiohttp session.


def _cursors_url() -> str:
    origin = get_api_origin()
    return f"{origin}/workflows/events/cursors"


async def get_resume_event_id(
    session_http: aiohttp.ClientSession,
    *,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[str]:
    """Fetch the appropriate cursor's Last-Event-ID for resuming the SSE stream.

    Resolution rules:
    - If session_id is provided, we fetch the session-specific cursor and return
      its eventId if present. This isolates session-level consumers.
    - Otherwise we fetch the project-wide cursor and return its eventId.
    - workspace_id can be provided instead of project_id; the server will
      resolve projectId (and possibly sessionId) from the workspace if available.

    Returns the eventId string to set as the Last-Event-ID header, or None.
    """
    params: Dict[str, str] = {}
    if workspace_id:
        params["workspaceId"] = workspace_id
    if project_id:
        params["projectId"] = project_id
    if session_id:
        params["sessionId"] = session_id

    url = _cursors_url()
    headers: Dict[str, str] = {}
    try:
        headers.update(get_auth_headers())
    except Exception as e:
        log_unique(f"⚠️ Could not resolve auth headers for cursors GET: {e}")

    try:
        async with session_http.get(url, headers=headers, params=params, timeout=15) as resp:
            if resp.status != 200:
                text = await _safe_text(resp)
                log_unique(
                    f"⚠️ Cursor GET failed ({resp.status}) project_id={project_id} session_id={session_id} workspace_id={workspace_id}: {text[:200]}"
                )
                return None
            data = await _safe_json(resp)
            if not isinstance(data, dict):
                return None
            # The service returns { projectId, sessionId, project: {...}, session: {...} }
            # Prefer the session scope when session_id was requested; else use project scope
            if session_id:
                sess = data.get("session")
                if isinstance(sess, dict):
                    evt = sess.get("eventId") or sess.get("event_id")
                    return str(evt) if evt else None
                return None
            else:
                proj = data.get("project")
                if isinstance(proj, dict):
                    evt = proj.get("eventId") or proj.get("event_id")
                    return str(evt) if evt else None
                return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_unique(f"⚠️ Cursor GET error: {e}")
        return None


async def update_cursor(
    session_http: aiohttp.ClientSession,
    *,
    event_id: str,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    scope: str = "session",  # "session" | "project" | "both"
    timestamp: Optional[str] = None,
) -> bool:
    """Persist a cursor position to the remote service.

    - scope determines which document(s) to update:
      - "session": update session-specific cursor (requires session_id)
      - "project": update project-wide cursor
      - "both": update both
    - Provide either project_id (preferred) or workspace_id.

    Returns True on success, False otherwise.
    """
    if not event_id:
        return False

    body: Dict[str, Any] = {"eventId": str(event_id)}
    if project_id:
        body["projectId"] = project_id
    if session_id:
        body["sessionId"] = session_id
    if workspace_id:
        body["workspaceId"] = workspace_id
    if timestamp is not None:
        body["timestamp"] = timestamp

    s = (scope or "").strip().lower()
    if s in ("session", "project", "both"):
        body["target"] = s
    else:
        # Default rule mirrors server logic: if sessionId present -> session else project
        if session_id:
            body["target"] = "session"
        else:
            body["target"] = "project"

    url = _cursors_url()
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    try:
        headers.update(get_auth_headers())
    except Exception as e:
        log_unique(f"⚠️ Could not resolve auth headers for cursors POST: {e}")

    try:
        async with session_http.post(url, headers=headers, json=body, timeout=15) as resp:
            if resp.status != 200:
                text = await _safe_text(resp)
                log_unique(
                    f"⚠️ Cursor POST failed ({resp.status}) project_id={project_id} session_id={session_id} workspace_id={workspace_id}: {text[:200]}"
                )
                return False
            # Best-effort; we don't require response body beyond status
            return True
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_unique(f"⚠️ Cursor POST error: {e}")
        return False


# Backward-compat shim APIs (no-ops/locals) ----------------------------------
# These remain only so legacy imports do not break during rollout. They do not
# persist to disk anymore and are not used by the updated consumer logic.

_legacy_state: Dict[str, Any] = {"projects": {}}


def load_cursors() -> Dict[str, Any]:
    """Legacy shim returning an in-memory dict structure.

    Note: This no longer reads from disk. For persistence, use get_resume_event_id
    and update_cursor with the remote service.
    """
    return _legacy_state


def save_cursors(_: Dict[str, Any]) -> None:
    """Legacy shim; no-op since we now persist remotely per project/session."""
    return None


# Internal helpers ------------------------------------------------------------

async def _safe_text(resp: aiohttp.ClientResponse) -> str:
    try:
        return await resp.text()
    except Exception:
        return ""


async def _safe_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json()
    except Exception:
        return None
