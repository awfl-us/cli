import os
import uuid
import time
import asyncio
from urllib.parse import urlparse

import aiohttp
import requests
import google.auth
from google.auth.transport.requests import Request as SyncRequest

from awfl.utils import log_unique
from .rh_utils import mask_headers


async def _get_gcloud_token() -> str:
    """Return a user access token via gcloud, honoring optional account.
    Respects:
      - GCLOUD_BIN (default: "gcloud")
      - CALLBACK_GCLOUD_ACCOUNT (optional)
    """
    import subprocess

    def _sync() -> str:
        gcloud_bin = os.environ.get("GCLOUD_BIN", "gcloud")
        account = os.environ.get("CALLBACK_GCLOUD_ACCOUNT")
        cmd = [gcloud_bin, "auth", "print-access-token"]
        if account:
            cmd += ["--account", account]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            raise RuntimeError(
                f"gcloud token failed rc={res.returncode} stderr={res.stderr.strip()}"
            )
        token = (res.stdout or "").strip()
        if not token:
            raise RuntimeError("gcloud token empty")
        return token

    return await asyncio.to_thread(_sync)


async def _get_adc_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])

    if not creds.valid:
        def _refresh():
            sess = requests.Session()
            creds.refresh(SyncRequest(session=sess))
        await asyncio.to_thread(_refresh)

    if not getattr(creds, "token", None):
        raise RuntimeError("no token after ADC refresh")

    return creds.token


async def _get_bearer_token() -> str:
    """Acquire a bearer token using either gcloud (if requested) or ADC."""
    use_gcloud = os.environ.get("CALLBACK_USE_GCLOUD_TOKEN") == "1"

    if use_gcloud:
        try:
            return await _get_gcloud_token()
        except Exception:
            pass  # fall back to ADC silently

    return await _get_adc_token()


async def post_callback(callback_url: str, payload: dict, *, correlation_id: str | None = None):
    cid = correlation_id or os.environ.get("CALLBACK_CORRELATION_ID") or uuid.uuid4().hex[:8]
    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        parsed = urlparse(callback_url)
        host = parsed.netloc

        timeout_total = int(os.environ.get("CALLBACK_TIMEOUT_SECONDS", "25"))
        connect_timeout = int(os.environ.get("CALLBACK_CONNECT_TIMEOUT_SECONDS", "5"))
        timeout = aiohttp.ClientTimeout(
            total=timeout_total,
            connect=connect_timeout,
            sock_read=max(1, timeout_total - connect_timeout),
        )

        # Attach OAuth token for Google APIs (e.g., Workflows callbacks)
        if host.endswith("googleapis.com"):
            token = await _get_bearer_token()
            headers["Authorization"] = f"Bearer {token}"

        debug_headers = os.environ.get("CALLBACK_LOG_HEADERS") == "1"
        if debug_headers:
            log_unique(f"[callback:req] POST {callback_url} headers={mask_headers(headers)} cid={cid}")

        async def _do_post(session: aiohttp.ClientSession, url: str):
            t0 = time.time()
            async with session.post(url, json=payload, headers=headers) as resp:
                body_text = await resp.text()  # drain body
                dt = int((time.time() - t0) * 1000)
                if debug_headers:
                    # Log subset of response headers; avoid dumping large maps
                    resp_h = {k: v for k, v in resp.headers.items() if k.lower() in {"date", "server", "content-type", "content-length"}}
                    log_unique(f"[callback:resp] {resp.status} in {dt}ms for {url} resp_headers={resp_h} body_len={len(body_text)} cid={cid}")
                return resp.status

        async with aiohttp.ClientSession(timeout=timeout) as session:
            status = await _do_post(session, callback_url)

            # Defensive retry: if we get a 404 and the callback id uses an underscore, try a hyphen.
            if (
                status == 404
                and host.endswith("googleapis.com")
                and "/callbacks/" in parsed.path
            ):
                tail = callback_url.rsplit("/", 1)[-1]
                if "_" in tail:
                    alt_tail = tail.replace("_", "-", 1)
                    alt_url = callback_url[: -len(tail)] + alt_tail
                    log_unique("404 on callback URL; retrying once with hyphenated ID")
                    await _do_post(session, alt_url)

    except asyncio.TimeoutError:
        log_unique(f"Callback POST timed out [cid={cid}] for {callback_url}")
    except Exception as e:
        log_unique(f"Failed to POST callback [cid={cid}] to {callback_url}: {e}")
