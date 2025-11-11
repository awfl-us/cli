import os
import asyncio

import aiohttp

from awfl.utils import get_api_origin
from awfl.auth import get_auth_headers


async def post_internal_callback(callback_id: str, payload: dict, *, correlation_id: str | None = None):
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

        async with aiohttp.ClientSession(timeout=timeout) as session:
            max_attempts = 2  # initial try + one fixed-delay retry
            for attempt in range(1, max_attempts + 1):
                try:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        status = resp.status
                        # Success
                        if status < 400:
                            return

                        # Transient statuses eligible for single retry
                        is_transient = (status == 429) or (500 <= status < 600)

                        if not is_transient or attempt == max_attempts:
                            return

                        # Fixed delay before the one-and-only retry
                        await asyncio.sleep(retry_delay_ms / 1000.0)
                        continue

                except (asyncio.TimeoutError, aiohttp.ClientError):
                    if attempt == max_attempts:
                        return
                    await asyncio.sleep(retry_delay_ms / 1000.0)
                    continue
                except Exception:
                    # Unknown error: do not retry
                    return

    except Exception:
        # Setup failure – nothing else to do
        return
