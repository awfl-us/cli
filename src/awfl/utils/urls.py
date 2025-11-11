import os
import requests
from typing import Optional

from .logging import log_unique, _is_debug

# We read the workflow env suffix directly from awfl.state to avoid circular deps
try:
    from awfl.state import get_workflow_env_suffix as _state_get_env_suffix
except Exception:  # pragma: no cover - during early bootstrap
    _state_get_env_suffix = lambda: os.getenv("WORKFLOW_ENV", "")  # type: ignore


def _get_env_suffix() -> str:
    suffix = _state_get_env_suffix()
    if suffix is None:
        suffix = os.getenv("WORKFLOW_ENV", "")
    return suffix or ""


def get_api_origin() -> str:
    """Origin for API calls. May include a path prefix.

    Behavior:
    - If API_ORIGIN is set, return it (sans trailing '/'). Allowed to include '/api'.
    - In Dev mode (WORKFLOW_ENV non-empty), default to http://localhost:5050/api
    - In Prod mode (no WORKFLOW_ENV), default to https://api.awfl.us

    Callers MUST construct paths without hardcoding '/api'. Always append '/workflows/...'.
    Examples:
      origin = http://localhost:5050/api -> f"{origin}/workflows/..." -> http://localhost:5050/api/workflows/...
      origin = https://api.awfl.us       -> f"{origin}/workflows/..." -> https://api.awfl.us/workflows/...
    """
    # Prefer explicit API_ORIGIN if provided
    origin_env = os.getenv("API_ORIGIN")
    if origin_env:
        return origin_env.rstrip('/')

    # Determine mode via per-process/workflow env suffix
    suffix = _get_env_suffix()

    # Dev mode: include '/api' path prefix for local backend
    if suffix:
        return "http://localhost:5050/api"

    # Prod mode: public API origin (no '/api' path prefix)
    return "https://api.awfl.us"


__all__ = ["get_api_origin"]
