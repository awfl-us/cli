import os
from awfl.utils import log_unique


def is_debug() -> bool:
    val = os.getenv("AWFL_SSE_DEBUG", "0").strip().lower()
    return val in ("1", "true", "yes", "on", "debug")


def dbg(msg: str) -> None:
    if is_debug():
        log_unique(f"🔎 [SSE dbg] {msg}")
