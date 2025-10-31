import os
import requests
from typing import Optional

from .logging import log_unique, _is_debug

# We read the workflow env suffix directly from state to avoid circular deps
try:
    from state import get_workflow_env_suffix as _state_get_env_suffix
except Exception:  # pragma: no cover - during early bootstrap
    _state_get_env_suffix = lambda: os.getenv("WORKFLOW_ENV", "")  # type: ignore


def get_base_url() -> str:
    """Get BASE_URL from environment, ngrok tunnel, or fallback to production."""
    # First try environment variable (for production/CI)
    base_url = os.getenv("BASE_URL")
    if base_url:
        return base_url

    # Try to detect local ngrok tunnel
    try:
        response = requests.get("http://localhost:4040/api/tunnels", timeout=2)
        if response.status_code == 200:
            tunnels = response.json().get("tunnels", [])
            for tunnel in tunnels:
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
    except Exception:
        pass

    # Fallback to production
    return "https://us-central1-topaigents.cloudfunctions.net"


def _get_env_suffix() -> str:
    suffix = _state_get_env_suffix()
    if suffix is None:
        suffix = os.getenv("WORKFLOW_ENV", "")
    return suffix or ""


def get_api_origin() -> str:
    """Origin for API calls (scheme+host+port only; no trailing path).

    Defaults by mode:
    - Dev (WORKFLOW_ENV non-empty, typically 'Dev'): prefer local ngrok https; else http://localhost:5050
    - Prod (no WORKFLOW_ENV): https://topaigents.com

    Environment override: API_ORIGIN takes precedence in all modes. If it ends with '/api',
    we normalize by stripping the '/api' suffix to avoid double '/api' when assembling URLs.
    """
    # Prefer explicit API_ORIGIN if provided
    origin_env = os.getenv("API_ORIGIN")
    if origin_env:
        raw = origin_env
        origin = origin_env.rstrip('/')
        # Normalize: strip trailing '/api' if present to avoid '/api/api/...'
        if origin.endswith('/api'):
            origin = origin[:-4]
            if _is_debug():
                log_unique(f"AWFL_DEBUG: Normalized API_ORIGIN from '{raw}' to '{origin}' (stripped trailing /api)")
        return origin

    # Determine mode via per-process/workflow env suffix
    suffix = _get_env_suffix()

    # Dev mode: use local dev defaults (ngrok -> localhost)
    if suffix:
        # Try to detect local ngrok tunnel (https)
        try:
            response = requests.get("http://localhost:4040/api/tunnels", timeout=2)
            if response.status_code == 200:
                tunnels = response.json().get("tunnels", [])
                for tunnel in tunnels:
                    if tunnel.get("proto") == "https":
                        return tunnel["public_url"].rstrip('/')
        except Exception:
            pass
        # Fallback to local dev server
        return "http://localhost:5050"

    # Prod mode: default to public API origin (no trailing path)
    return "https://topaigents.com"


__all__ = ["get_base_url", "get_api_origin"]
