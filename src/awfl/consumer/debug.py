import os
from awfl.utils import log_unique


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on", "debug")


def is_debug() -> bool:
    val = os.getenv("AWFL_SSE_DEBUG", "0")
    return _truthy(val)


def is_debug_raw() -> bool:
    """Stronger debug that allows logging full raw event payloads.

    Controlled via AWFL_SSE_DEBUG_RAW env var. Use with caution to avoid leaking sensitive data.
    """
    val = os.getenv("AWFL_SSE_DEBUG_RAW", "0")
    return _truthy(val)


def dbg(msg: str) -> None:
    if is_debug():
        log_unique(f"🔎 [SSE dbg] {msg}")
