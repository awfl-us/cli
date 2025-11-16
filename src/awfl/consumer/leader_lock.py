import os
import socket
import uuid
from typing import Any, Dict, Optional, Tuple

import aiohttp

from awfl.auth import get_auth_headers
from awfl.utils import get_api_origin

# Server-backed project consumer leader lock helpers
#
# This replaces the previous local file-based lock in ~/.awfl/locks.
# Minimal notes about the previous approach are kept here for context:
# - Old method used a PID-based lock file per project on the local machine.
# - That strategy failed across multiple machines or containers and could leave
#   stale locks after crashes or PID reuse. We now rely on a server-side lock
#   with a lease and explicit refresh/release operations.
#
# Endpoints (server-side):
#   POST /workflows/projects/:id/consumer-lock/acquire
#   POST /workflows/projects/:id/consumer-lock/release
# See API contract for detailed behavior.

_consumer_id: Optional[str] = None


def get_consumer_id() -> str:
    """Return a stable consumer id for this process.

    Resolution order:
    - AWFL_CONSUMER_ID env override if provided
    - Otherwise derived from hostname, pid, and a short random suffix
    The value is cached for the lifetime of the process.
    """
    global _consumer_id
    if _consumer_id:
        return _consumer_id
    override = os.getenv("AWFL_CONSUMER_ID")
    if override:
        _consumer_id = override.strip()
        return _consumer_id
    host = socket.gethostname()
    pid = os.getpid()
    rand = uuid.uuid4().hex[:8]
    _consumer_id = f"{host}-{pid}-{rand}"
    return _consumer_id


async def acquire_lock(
    session_http: aiohttp.ClientSession,
    *,
    project_id: str,
    consumer_id: Optional[str] = None,
    lease_ms: Optional[int] = None,
) -> Tuple[bool, bool, bool, Dict[str, Any]]:
    """Attempt to acquire or refresh the project consumer lock.

    Returns a tuple: (acquired, refreshed, conflict, payload)
    - acquired/refreshed are True on 200 OK success
    - conflict is True on 409 responses
    - payload is the parsed JSON body from the server (may be empty on errors)
    """
    url = f"{get_api_origin()}/workflows/projects/{project_id}/consumer-lock/acquire"
    cid = consumer_id or get_consumer_id()
    headers = {
        "Content-Type": "application/json",
    }
    # Add auth headers (best-effort)
    try:
        headers.update(get_auth_headers())
    except Exception:
        pass

    # Optional hints via headers per API
    if cid:
        headers["x-consumer-id"] = cid
    if lease_ms and lease_ms > 0:
        headers["x-lock-lease-ms"] = str(int(lease_ms))

    body: Dict[str, Any] = {}
    if cid:
        body["consumerId"] = cid
    if lease_ms and lease_ms > 0:
        body["leaseMs"] = int(lease_ms)

    try:
        async with session_http.post(url, json=body, headers=headers) as resp:
            status = resp.status
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}
            if status == 200:
                # Success: either acquired or refreshed
                acquired = bool(data.get("acquired"))
                refreshed = bool(data.get("refreshed"))
                return acquired, refreshed, False, data
            elif status == 409:
                return False, False, True, data
            else:
                # Treat other codes as not acquired and not a conflict
                return False, False, False, data
    except Exception:
        # Network/other error: not acquired, not a formal conflict
        return False, False, False, {}


async def release_lock(
    session_http: aiohttp.ClientSession,
    *,
    project_id: str,
    consumer_id: Optional[str] = None,
    force: bool = False,
) -> Tuple[bool, bool, bool, Dict[str, Any]]:
    """Release the project consumer lock.

    Returns (ok, released, conflict, payload)
    - ok True when server responded 200 (regardless of released true/false)
    - conflict True for 409
    - payload is parsed JSON body
    """
    url = f"{get_api_origin()}/workflows/projects/{project_id}/consumer-lock/release"
    cid = consumer_id or get_consumer_id()

    headers = {
        "Content-Type": "application/json",
    }
    try:
        headers.update(get_auth_headers())
    except Exception:
        pass

    if cid:
        headers["x-consumer-id"] = cid
    if force:
        headers["x-lock-force"] = "1"

    body: Dict[str, Any] = {}
    if cid:
        body["consumerId"] = cid
    if force:
        body["force"] = True

    body["consumerType"] = "LOCAL"

    try:
        async with session_http.post(url, json=body, headers=headers) as resp:
            status = resp.status
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}
            if status == 200:
                released = bool(data.get("released", False))
                return True, released, False, data
            elif status == 409:
                return False, False, True, data
            else:
                return False, False, False, data
    except Exception:
        return False, False, False, {}
