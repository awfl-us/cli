import os
import uuid
import time
import asyncio

import aiohttp

from awfl.utils import log_unique, get_api_origin
from awfl.auth import get_auth_headers
from .rh_utils import mask_headers


async def post_internal_callback(callback_id: str, payload: dict, *, correlation_id: str | None = None):
    """POST callback payload to our internal server using user auth.

    - Uses get_api_origin() to build the base origin (no trailing /api).
    - Primary path: {origin}/api/workflows/callbacks/{callback_id}.
    - Fallback path (on 404): {origin}/workflows/callbacks/{callback_id}.
    - Adds Firebase user Authorization header (or X-Skip-Auth) and x-project-id via get_auth_headers().
    - Respects CALLBACK_TIMEOUT_SECONDS / CALLBACK_CONNECT_TIMEOUT_SECONDS for timeouts.
    """
    cid = correlation_id or os.environ.get("CALLBACK_CORRELATION_ID") or uuid.uuid4().hex[:8]
    origin = (get_api_origin() or "").rstrip('/')
    primary_url = f"{origin}/api/workflows/callbacks/{callback_id}"
    fallback_url = f"{origin}/workflows/callbacks/{callback_id}"

    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # Merge auth headers (Authorization or X-Skip-Auth + x-project-id)
        try:
            auth_headers = get_auth_headers() or {}
            headers.update(auth_headers)
        except Exception as e:
            # If we cannot resolve user auth, surface an error in logs but still attempt without it
            log_unique(f"post_internal_callback: failed to resolve auth headers: {e}")

        timeout_total = int(os.environ.get("CALLBACK_TIMEOUT_SECONDS", "25"))
        connect_timeout = int(os.environ.get("CALLBACK_CONNECT_TIMEOUT_SECONDS", "5"))
        timeout = aiohttp.ClientTimeout(
            total=timeout_total,
            connect=connect_timeout,
            sock_read=max(1, timeout_total - connect_timeout),
        )

        debug_headers = os.environ.get("CALLBACK_LOG_HEADERS") == "1"

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async def _post(url: str) -> int:
                if debug_headers:
                    log_unique(f"[callback:int:req] POST {url} headers={mask_headers(headers)} cid={cid}")
                t0 = time.time()
                async with session.post(url, json=payload, headers=headers) as resp:
                    body_text = await resp.text()
                    dt = int((time.time() - t0) * 1000)
                    if debug_headers:
                        resp_h = {k: v for k, v in resp.headers.items() if k.lower() in {"date", "server", "content-type", "content-length"}}
                        log_unique(f"[callback:int:resp] {resp.status} in {dt}ms for {url} resp_headers={resp_h} body_len={len(body_text)} cid={cid}")
                    if resp.status >= 400:
                        log_unique(f"Internal callback returned HTTP {resp.status} for {url} [cid={cid}] body_len={len(body_text)}")
                    return resp.status

            status = await _post(primary_url)
            if status == 404 and primary_url != fallback_url:
                # Retry once without '/api' prefix for dev servers running on a different port/path
                log_unique("Internal callback 404 on primary path; retrying without /api prefix")
                await _post(fallback_url)

    except asyncio.TimeoutError:
        log_unique(f"Internal callback POST timed out [cid={cid}] for {primary_url}")
    except Exception as e:
        log_unique(f"Failed to POST internal callback [cid={cid}] to {primary_url}: {e}")
