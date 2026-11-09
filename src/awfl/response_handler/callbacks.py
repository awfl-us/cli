import os
import asyncio
from weakref import WeakKeyDictionary

import aiohttp

from awfl.utils import get_api_origin
from awfl.auth import get_auth_headers


# Keep callback HTTP connections alive across short-lived MCP operations.
# Cache is scoped to the running event loop: aiohttp sessions cannot safely
# cross loops. Headers are NOT cached, so refreshed Firebase credentials are
# resolved for each callback. The CLI closes these sessions at shutdown.
_callback_sessions = WeakKeyDictionary()


def _callback_http_session():
    loop = asyncio.get_running_loop()
    session = _callback_sessions.get(loop)
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(
            limit=32, limit_per_host=16, ttl_dns_cache=300, keepalive_timeout=60,
        )
        session = aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=None),
        )
        _callback_sessions[loop] = session
    return session


async def close_callback_session():
    """Close pooled callback connections owned by the current event loop."""
    session = _callback_sessions.pop(asyncio.get_running_loop(), None)
    if session and not session.closed:
        await session.close()


async def post_internal_callback(callback_id: str, payload: dict, *, correlation_id: str | None = None, execution_url=None, execution_token=None):
    """POST callback payload to our internal server using user auth.

    - Uses get_api_origin() to build the base origin (dev includes '/api', prod does not).
    - Path: {origin}/workflows/callbacks/{callback_id} (no fallback paths).
    - Adds Firebase user Authorization header (or X-Skip-Auth) and x-project-id via get_auth_headers().
    - Respects CALLBACK_TIMEOUT_SECONDS / CALLBACK_CONNECT_TIMEOUT_SECONDS for per-attempt timeouts.
    - Minimal retry: one attempt plus at most one fixed-delay retry on transient errors (429, 5xx) or network/timeout errors.
    - No logging.
    """
    _ = correlation_id  # kept for signature compatibility

    origin = (get_api_origin() or "").rstrip('/')
    url = f"{origin}/workflows/callbacks/{callback_id}"

    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # Merge auth headers (Authorization or X-Skip-Auth + x-project-id)
        try:
            auth_headers = get_auth_headers() or {}
            headers.update(auth_headers)
        except Exception:
            # If we cannot resolve user auth, still attempt without it
            pass

        timeout_total = int(os.environ.get("CALLBACK_TIMEOUT_SECONDS", "25"))
        connect_timeout = int(os.environ.get("CALLBACK_CONNECT_TIMEOUT_SECONDS", "5"))
        retry_delay_ms = int(os.environ.get("CALLBACK_RETRY_DELAY_MS", "500"))

        timeout = aiohttp.ClientTimeout(
            total=timeout_total,
            connect=connect_timeout,
            sock_read=max(1, timeout_total - connect_timeout),
        )

        session = _callback_http_session()
        max_attempts = 2  # initial try + one fixed-delay retry
        for attempt in range(1, max_attempts + 1):
            try:
                async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                    status = resp.status
                    # Consume small callback acknowledgements so the keep-alive
                    # socket is available immediately for the next operation.
                    await resp.read()
                    if status < 400:
                        return True

                    # Transient statuses eligible for single retry
                    is_transient = (status == 429) or (500 <= status < 600)

                    if not is_transient or attempt == max_attempts:
                        return False

                    await asyncio.sleep(retry_delay_ms / 1000.0)
                    continue

            except (asyncio.TimeoutError, aiohttp.ClientError):
                if attempt == max_attempts:
                    return False
                await asyncio.sleep(retry_delay_ms / 1000.0)
                continue
            except Exception:
                # Unknown error: do not retry
                return False

    except Exception:
        # Setup failure – nothing else to do
        return False
