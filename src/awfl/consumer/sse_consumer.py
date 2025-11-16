import asyncio
import json
import os
import random
import contextlib
from typing import Optional, Tuple

import aiohttp

from awfl.auth import get_auth_headers
from awfl.response_handler import get_session
from awfl.utils import get_api_origin, log_unique
from awfl.events.workspace import resolve_project_id, get_or_create_workspace

from .cursors import get_resume_event_id, update_cursor
from .sse_parser import SSEParser
from .leader_lock import get_consumer_id, acquire_lock, release_lock
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
    - For project scope, ensures single-leader per project using a server-side lease lock.
    - For session scope, will NOT create a project if missing; waits until the project exists to avoid duplicate creation.
    - Robust reconnection with backoff and jitter; reacts to session change for session scope.

    Returns a small string status on termination to help the caller classify the outcome:
    - "skipped-lock": Project consumer skipped because another instance holds the leader lock (benign).
    - "lost-lock": Project consumer lost the server lock while running (fatal at top level).
    - "cancelled": Task was cancelled (shutdown) (benign).
    - "ended": Stream ended and loop exited (unexpected).
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

    # Configure socket timeouts to recover from half-open connections after sleep
    try:
        sock_read_timeout = float(os.getenv("AWFL_SSE_SOCK_READ_TIMEOUT_SECS", "90"))
        if sock_read_timeout <= 0:
            client_timeout = aiohttp.ClientTimeout(total=None)
        else:
            client_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=sock_read_timeout)
    except Exception:
        client_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=90)

    # Application-level idle stall watchdog (covers cases where sock_read timeout fails on macOS sleep)
    try:
        idle_stall_secs = float(os.getenv("AWFL_SSE_IDLE_STALL_SECS", "75"))
    except Exception:
        idle_stall_secs = 75.0

    # Lock lease and refresh tuning
    def _clamp_lease(ms: int) -> int:
        # Server bounds: min 5s, default 45s, max 10m
        return max(5000, min(ms, 600000))

    try:
        lease_ms = int(os.getenv("AWFL_LOCK_LEASE_MS", "45000"))
    except Exception:
        lease_ms = 45000
    lease_ms = _clamp_lease(lease_ms)

    try:
        refresh_interval_secs = float(os.getenv("AWFL_LOCK_REFRESH_INTERVAL_SECS", "31"))
    except Exception:
        refresh_interval_secs = 31.0
    # Safety: never refresh at/after expiry; keep at least 5s headroom
    lease_secs = lease_ms / 1000.0
    refresh_interval_secs = min(refresh_interval_secs, max(1.0, lease_secs - 5.0))

    async with aiohttp.ClientSession(timeout=client_timeout) as session_http:
        project_id_for_lock: Optional[str] = None
        leader_acquired = False
        lost_lock = False
        refresher_task: Optional[asyncio.Task] = None

        # Parent task handle for cooperative cancellation
        parent_task = asyncio.current_task()

        async def _start_or_confirm_lock(project_id: str) -> Optional[str]:
            nonlocal leader_acquired
            nonlocal refresher_task
            nonlocal lost_lock

            # Attempt to acquire/refresh lock; retry transiently if unknown status
            attempt = 0
            while True:
                attempt += 1
                acquired, refreshed, conflict, payload = await acquire_lock(
                    session_http,
                    project_id=project_id,
                    lease_ms=lease_ms,
                )
                if conflict:
                    # Another consumer holds the lock
                    # Try to include expiry hint if present
                    expires_in = None
                    try:
                        lock = (payload or {}).get("lock") or {}
                        expires_in = lock.get("expiresInMs") or lock.get("expiresIn")
                    except Exception:
                        expires_in = None
                    if expires_in is not None:
                        log_unique(
                            f"🦬 Project-wide SSE already active for project {project_id}; skipping in this terminal (lock expires in ~{int(expires_in)/1000:.0f}s)."
                        )
                    else:
                        log_unique(
                            f"🦬 Project-wide SSE already active for project {project_id}; skipping in this terminal."
                        )
                    return "skipped-lock"
                if acquired or refreshed:
                    leader_acquired = True
                    cid = get_consumer_id()
                    if acquired:
                        log_unique(
                            f"🔐 Acquired project consumer lock for {project_id} as {cid} (lease={lease_ms}ms)"
                        )
                    else:
                        log_unique(
                            f"🔄 Refreshed project consumer lock for {project_id} as {cid} (lease={lease_ms}ms)"
                        )
                    # Start refresher if not already running
                    if not refresher_task or refresher_task.done():
                        refresher_task = asyncio.create_task(
                            _lease_refresher(project_id), name="awfl-lock-refresher"
                        )
                    return None
                # Unknown/other response; backoff and retry a few times, then keep trying with capped backoff
                delay = min(5.0, 0.5 * attempt) + random.random()
                log_unique(
                    f"⚠️ Lock acquire/refresh returned indeterminate status (attempt {attempt}); retrying in ~{delay:.1f}s"
                )
                await asyncio.sleep(delay)

        # lease refresher impl
        async def _lease_refresher(project_id: str):
            nonlocal lost_lock

            last_success = asyncio.get_event_loop().time()
            while True:
                await asyncio.sleep(refresh_interval_secs)
                acquired, refreshed, conflict, _payload = await acquire_lock(
                    session_http,
                    project_id=project_id,
                    lease_ms=lease_ms,
                )
                now = asyncio.get_event_loop().time()
                if conflict:
                    log_unique(
                        "❌ Lost project consumer lock due to conflict with another active consumer; terminating."
                    )
                    # signal fatal termination by cancelling parent task
                    # nonlocal lost_lock
                    lost_lock = True
                    if parent_task:
                        parent_task.cancel()
                    return
                if acquired or refreshed:
                    last_success = now
                    # Optional: keep refresh logs light
                    dbg(
                        f"Refreshed project lock for {project_id} (acquired={acquired}, refreshed={refreshed})"
                    )
                else:
                    # Transient failure; if we've exceeded lease window since last success, treat as lost
                    since_ok = now - last_success
                    if since_ok > lease_secs:
                        log_unique(
                            "❌ Lost project consumer lock (lease expired without successful refresh); terminating."
                        )
                        # nonlocal lost_lock
                        lost_lock = True
                        if parent_task:
                            parent_task.cancel()
                        return
                    else:
                        log_unique(
                            f"⚠️ Lock refresh failed; will retry (grace ~{int(lease_secs - since_ok)}s remaining)"
                        )

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

            # For project-wide scope, ensure only one live consumer per project using server lock
            if scope == "project":
                project_id_for_lock = project_id
                if not leader_acquired:
                    reason = await _start_or_confirm_lock(project_id_for_lock)
                    if reason == "skipped-lock":
                        return "skipped-lock"  # benign skip

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
                async with session_http.get(stream_url, headers=headers, params=params) as resp:
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

                    # Idle stall watchdog task; force-close response if no bytes are seen for idle_stall_secs
                    last_activity = asyncio.get_event_loop().time()

                    async def _idle_watchdog():
                        if idle_stall_secs and idle_stall_secs > 0:
                            # Check roughly every 5 seconds (or 1/3 of stall window if small)
                            interval = max(1.0, min(5.0, idle_stall_secs / 3.0))
                            while True:
                                await asyncio.sleep(interval)
                                now = asyncio.get_event_loop().time()
                                if now - last_activity > idle_stall_secs:
                                    log_unique(
                                        f"🧊 SSE idle for ~{int(now - last_activity)}s (threshold={int(idle_stall_secs)}s); forcing reconnect..."
                                    )
                                    try:
                                        resp.close()
                                    except Exception:
                                        pass
                                    break

                    idle_task = asyncio.create_task(_idle_watchdog(), name="sse-idle-watchdog")
                    try:
                        async for raw in resp.content:
                            # mark activity for watchdog
                            last_activity = asyncio.get_event_loop().time()

                            # If user switches CLI session, reconnect to new workspace (session scope only)
                            if scope == "session":
                                try:
                                    current_session_id = get_session()
                                except Exception:
                                    current_session_id = last_session_id
                                if current_session_id != last_session_id:
                                    log_unique("🔄 Session changed; reconnecting SSE for new workspace...")
                                    break

                            # If lost lock was signaled while streaming, break to unwind
                            if scope == "project" and lost_lock:
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
                    finally:
                        if not idle_task.done():
                            idle_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await idle_task

                    # If we exit the async for, the connection closed or workspace changed. Fall through to reconnect.
                    log_unique("ℹ️ SSE connection ended; reconnecting...")

            except asyncio.CancelledError:
                # Task canceled: exit cleanly
                if scope == "project" and project_id_for_lock and leader_acquired:
                    try:
                        ok, released, conflict, _ = await release_lock(
                            session_http, project_id=project_id_for_lock
                        )
                        if ok and released:
                            log_unique("🔓 Released project consumer lock")
                        elif ok and not released:
                            log_unique("ℹ️ Lock release reported no active lock (already released or expired)")
                        elif conflict:
                            log_unique("⚠️ Lock release conflict; another holder present")
                        else:
                            log_unique("⚠️ Lock release failed")
                    except Exception:
                        log_unique("⚠️ Failed to release project consumer lock")
                if scope == "project" and lost_lock:
                    return "lost-lock"
                log_unique("🛑 SSE consumer canceled; closing.")
                return "cancelled"
            except asyncio.TimeoutError:
                # Socket read timeout (likely sleep/half-open). Reconnect.
                log_unique("⌛ SSE read timed out; reconnecting to recover from possible sleep/half-open state...")
                await asyncio.sleep(min(backoff, 5.0) + random.random())
                backoff = min(backoff * 2, backoff_max)
            except Exception as e:
                # Network or parsing error -> backoff and retry
                log_unique(f"⚠️ SSE error: {e}; reconnecting in ~{backoff:.1f}s")
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, backoff_max)

            # Small delay before attempting a reconnect
            await asyncio.sleep(0.2)

        # Not reached, but ensure lock release
        if scope == "project" and project_id_for_lock and leader_acquired:
            try:
                ok, released, conflict, _ = await release_lock(session_http, project_id=project_id_for_lock)
                if ok and released:
                    log_unique("🔓 Released project consumer lock")
            except Exception:
                pass
        return "ended"
