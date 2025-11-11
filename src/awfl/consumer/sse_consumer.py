import asyncio
import json
import random
from typing import Optional, Tuple

import aiohttp

from awfl.auth import get_auth_headers
from awfl.response_handler import get_session
from awfl.utils import get_api_origin, log_unique
from awfl.events.workspace import resolve_project_id, get_or_create_workspace

from .cursors import get_resume_event_id, update_cursor
from .sse_parser import SSEParser
from .leader_lock import try_acquire_project_leader, release_project_leader
from .routing import forward_event
from .debug import dbg, is_debug


async def _resolve_project_and_workspace(
    session_http: aiohttp.ClientSession,
    forced_session_id: Optional[str],
    *,
    create_project_if_missing: bool,
) -> Tuple[Optional[str], Optional[str]]:
    project_id = await resolve_project_id(session_http, create_if_missing=create_project_if_missing)
    if not project_id:
        return None, None
    ws_id = await get_or_create_workspace(session_http, project_id, session_id=forced_session_id)
    return project_id, ws_id


async def consume_events_sse(
    stream_url: Optional[str] = None,
    scope: str = "session",  # "session" | "project"
):
    """Connect to the awfl-relay SSE stream (workspace-based) and forward events.

    New model (no background concept):
    - Project-scope consumer executes tool side effects for all sessions, silently (no logs).
    - Session-scope consumers only log events (no execution) so multiple terminals can display progress.

    Mechanics:
    - Resolves project by normalized git remote.
    - Resolves/registers a workspace per project and desired scope (session or project-wide).
    - Connects to /workflows/events/stream?workspaceId=... and resumes via Last-Event-ID per project/session.
    - For project scope, ensures single-leader per project using a local lock.
    - For session scope, will NOT create a project if missing; waits until the project exists to avoid duplicate creation.
    - Robust reconnection with backoff and jitter; reacts to session change for session scope.
    """
    # Resolve defaults
    if stream_url is None:
        origin = get_api_origin()
        stream_url = f"{origin}/workflows/events/stream"

    scope = (scope or "session").lower()

    # Creation policy: project stream is allowed to create; session stream must wait for it
    create_project_if_missing = (scope == "project")

    log_unique(
        f"🔌 Connecting to events stream (workspace mode, scope={scope}): {stream_url}"
    )
    if is_debug():
        log_unique("🔎 SSE debug enabled via AWFL_SSE_DEBUG=1")

    last_ws_id: Optional[str] = None
    last_session_id: Optional[str] = None
    backoff = 1.0
    backoff_max = 30.0

    async with aiohttp.ClientSession() as session_http:
        project_id_for_lock: Optional[str] = None
        leader_acquired = False

        while True:
            # Resolve project and workspace according to scope
            forced_session_id = None
            if scope == "session":
                try:
                    forced_session_id = get_session()
                except Exception:
                    forced_session_id = None
            else:
                forced_session_id = None

            project_id, ws_id = await _resolve_project_and_workspace(
                session_http,
                forced_session_id,
                create_project_if_missing=create_project_if_missing,
            )
            dbg(f"Resolved project_id={project_id}, ws_id={ws_id}, scope={scope}, create_if_missing={create_project_if_missing}")
            if not project_id or not ws_id:
                # Could not resolve project/workspace. For session scope, this likely means project is not created yet.
                if scope == "session":
                    log_unique("⏳ Waiting for project-wide consumer to create/resolve project...")
                await asyncio.sleep(min(backoff, 5.0))
                backoff = min(backoff * 2, backoff_max)
                continue

            # For project-wide scope, ensure only one live consumer per project on this machine
            if scope == "project":
                project_id_for_lock = project_id
                if not leader_acquired:
                    if not try_acquire_project_leader(project_id_for_lock):
                        log_unique(
                            f"🪄 Project-wide SSE already active for project {project_id_for_lock}; skipping in this terminal."
                        )
                        return  # do not consume project-wide in this process
                    leader_acquired = True
                    dbg(f"Acquired project-wide leader lock for {project_id_for_lock}")

            # Reset backoff when switching workspaces to be responsive
            if ws_id != last_ws_id:
                backoff = 1.0

            params = {"workspaceId": ws_id}

            headers = {"Accept": "text/event-stream"}
            try:
                headers.update(get_auth_headers())
            except Exception as e:
                log_unique(f"⚠️ Could not resolve auth headers for SSE: {e}")

            # Attach Last-Event-ID cursor if available for this workspace and scope
            try:
                if scope == "session":
                    resume_id = await get_resume_event_id(
                        session_http,
                        project_id=project_id,
                        session_id=forced_session_id,
                        workspace_id=ws_id,
                    )
                else:
                    resume_id = await get_resume_event_id(
                        session_http,
                        project_id=project_id,
                        workspace_id=ws_id,
                    )
            except Exception as e:
                log_unique(f"⚠️ Failed to get resume cursor: {e}")
                resume_id = None

            if resume_id:
                headers["Last-Event-ID"] = str(resume_id)

            try:
                last_session_id = get_session()
            except Exception:
                last_session_id = None

            dbg(
                f"GET {stream_url} params={params} Last-Event-ID={'set' if resume_id else 'none'}"
            )

            try:
                async with session_http.get(stream_url, headers=headers, params=params, timeout=None) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        log_unique(f"❌ SSE connect failed ({resp.status}): {text[:500]}")
                        # Backoff before retry
                        await asyncio.sleep(backoff + random.random())
                        backoff = min(backoff * 2, backoff_max)
                        continue

                    last_ws_id = ws_id
                    log_unique(
                        f"✅ SSE connected (workspace={ws_id}, scope={scope}). Resuming after id={resume_id or 'None'}"
                    )

                    parser = SSEParser()
                    evt_count = 0

                    async for raw in resp.content:
                        # If user switches CLI session, reconnect to new workspace (session scope only)
                        if scope == "session":
                            try:
                                current_session_id = get_session()
                            except Exception:
                                current_session_id = last_session_id
                            if current_session_id != last_session_id:
                                log_unique("🔄 Session changed; reconnecting SSE for new workspace...")
                                break

                        try:
                            line = raw.decode("utf-8", errors="ignore")
                        except Exception:
                            # If decoding fails, skip chunk
                            continue

                        for l in line.splitlines(True):  # keepends True to preserve newlines
                            evt = parser.feed_line(l)
                            if evt is None:
                                continue

                            # Dispatch complete SSE event
                            evt_id = evt.get("id")
                            data_text = evt.get("data") or ""
                            evt_type = evt.get("event") or "message"

                            if not data_text.strip():
                                # Ignore empty data events (e.g., heartbeat edge cases)
                                dbg("Empty data event; ignored")
                                continue
                            evt_count += 1
                            dbg(
                                f"evt#{evt_count} (type={evt_type}) id={evt_id} data_len={len(data_text)} preview={data_text[:160].replace('\n',' ')}"
                            )
                            try:
                                obj = json.loads(data_text)
                            except Exception as e:
                                log_unique(
                                    f"⚠️ SSE event JSON parse error: {e}. data[0:200]={data_text[:200]}"
                                )
                                continue

                            # Forward to CLI response handler according to scope
                            try:
                                mode = "execute" if scope == "project" else "log"
                                await forward_event(obj, mode=mode)  # project executes silently; session logs only
                            except Exception as e:
                                log_unique(f"⚠️ Error handling SSE event: {e}")

                            # Persist new cursor remotely per project/session
                            if evt_id:
                                # Prefer server-provided create_time if present; else fall back to local time string
                                ts = obj.get("create_time") or obj.get("time")
                                if ts is None:
                                    try:
                                        # Avoid import at top to keep light; local import
                                        import time as _time

                                        ts = str(_time.time())
                                    except Exception:
                                        ts = ""
                                try:
                                    if scope == "session":
                                        await update_cursor(
                                            session_http,
                                            event_id=str(evt_id),
                                            project_id=project_id,
                                            session_id=forced_session_id,
                                            workspace_id=ws_id,
                                            scope="session",
                                            timestamp=str(ts) if ts is not None else None,
                                        )
                                    else:
                                        await update_cursor(
                                            session_http,
                                            event_id=str(evt_id),
                                            project_id=project_id,
                                            workspace_id=ws_id,
                                            scope="project",
                                            timestamp=str(ts) if ts is not None else None,
                                        )
                                except Exception as e:
                                    log_unique(f"⚠️ Failed to update cursor: {e}")

                    # If we exit the async for, the connection closed or workspace changed. Fall through to reconnect.
                    log_unique("ℹ️ SSE connection ended; reconnecting...")

            except asyncio.CancelledError:
                # Task canceled: exit cleanly
                log_unique("🛑 SSE consumer canceled; closing.")
                # Release leader lock if held
                if scope == "project" and project_id_for_lock and leader_acquired:
                    release_project_leader(project_id_for_lock)
                return
            except Exception as e:
                # Network or parsing error -> backoff and retry
                log_unique(f"⚠️ SSE error: {e}; reconnecting in ~{backoff:.1f}s")
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, backoff_max)

            # Small delay before attempting a reconnect
            await asyncio.sleep(0.2)

        # Not reached, but ensure lock release
        if scope == "project" and project_id_for_lock and leader_acquired:
            release_project_leader(project_id_for_lock)
