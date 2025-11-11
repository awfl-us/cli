import os
import requests
from typing import Dict, Any

from awfl.auth import get_auth_headers
from awfl.state import set_active_execution, get_workflow_env_suffix as _state_get_env_suffix

from .logging import log_unique, _is_debug
from .urls import get_api_origin


def _get_workflow_env_suffix() -> str:
    """Return the environment suffix to apply to workflow names.
    Precedence: state (per-process) > env var WORKFLOW_ENV > default "" (prod).
    """
    try:
        suffix = _state_get_env_suffix()
    except Exception:
        suffix = os.getenv("WORKFLOW_ENV", "")
    return suffix or ""


def _strip_env_suffix(name: str, suffix: str) -> str:
    if name and suffix and name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def _ensure_env_suffix(name: str, suffix: str) -> str:
    if name and suffix and not name.endswith(suffix):
        return name + suffix
    return name


def _mask_auth_header(headers: dict) -> dict:
    masked = {}
    for k, v in (headers or {}).items():
        if k.lower() == "authorization" and isinstance(v, str):
            masked[k] = "Bearer ***"
        else:
            masked[k] = v
    return masked


def trigger_workflow(name: str, data: Dict[str, Any]):
    """Trigger a workflow. Supports two modes:
    - api (default): POST /workflows/execute with Firebase Auth
    - gcloud: (not implemented here in refactor snapshot)

    Select via WORKFLOW_EXEC_MODE env var: 'api' (default) or 'gcloud'.

    IMPORTANT: Env suffixing is now centralized here to avoid double-appending.
      - api mode: send UNSUFFIXED workflowName (server will apply WORKFLOW_ENV)
      - gcloud mode: execute SUFFIXED workflow name locally (not implemented in this snapshot)
    """
    # Inject model from environment or use default
    model = os.getenv("LLM_MODEL", "gpt-5")
    data["model"] = model

    # Inject default fund for servicing requests unless provided by caller
    # Optional overrides via env: FUND or AWFL_FUND
    fund_value = None
    try:
        fund_env = os.getenv("FUND") or os.getenv("AWFL_FUND")
        if fund_env is not None and str(fund_env).strip() != "":
            fund_value = float(fund_env)
    except Exception:
        fund_value = None
    if "fund" not in data:
        data["fund"] = fund_value if fund_value is not None else 1

    # Always include userAuthToken parameter when available (Google Identity Platform ID token)
    # user_auth_token = None
    auth_headers_for_params = {}
    try:
        auth_headers_for_params = get_auth_headers()
        # user_auth_token = _extract_user_auth_token_from_headers(auth_headers_for_params)
    except Exception as e:
        # Non-fatal for param injection; continue without token
        log_unique(f"⚠️ Could not resolve user auth token for params: {e}")

    data["background"] = False

    exec_mode = os.getenv("WORKFLOW_EXEC_MODE", "api").lower()
    suffix = _get_workflow_env_suffix()

    if exec_mode == "api":
        # Server applies env suffix; ensure we DO NOT include it
        wf_name = _strip_env_suffix(name, suffix)
        origin = get_api_origin()
        url = f"{origin}/workflows/execute"
        payload = {
            "workflowName": wf_name,
            "params": data,
            "sync": True,
        }
        try:
            headers = {"Content-Type": "application/json"}
            # Reuse the headers we already resolved if present; else resolve now
            if auth_headers_for_params:
                headers.update(auth_headers_for_params)
            else:
                headers.update(get_auth_headers())
        except Exception as e:
            log_unique(f"❌ Auth initialization failed: {e}")
            return

        if _is_debug():
            masked = _mask_auth_header(headers)
            log_unique(
                "AWFL_DEBUG: Executing via API"
                + f"\n  exec_mode=api wf_name={wf_name} suffix={suffix!r}"
                + f"\n  origin={origin}"
                + f"\n  url={url}"
                + f"\n  headers={masked}"
                + f"\n  params.keys={list(payload.keys())}"
            )

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                except Exception:
                    err_body = resp.text
                if _is_debug():
                    req_url = getattr(resp.request, 'url', url)
                    ct = resp.headers.get('content-type')
                    via = resp.headers.get('via')
                    log_unique(
                        "AWFL_DEBUG: API error"
                        + f"\n  status={resp.status_code} content-type={ct} via={via}"
                        + f"\n  request_url={req_url}"
                        + f"\n  response_body={str(err_body)[:500]}"
                    )
                log_unique(f"❌ API execute failed ({resp.status_code}): {err_body}")
                return

            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text}

            # Try to extract an execution name if the server returns one
            execution_name = None
            if isinstance(body, dict):
                execution_name = body.get("executionName") or body.get("name")
                if not execution_name and isinstance(body.get("execution"), dict):
                    execution_name = body["execution"].get("name")

            if execution_name:
                log_unique(f"🚀 Execution started: {execution_name}")
            else:
                log_unique(f"🚀 Execution requested for {wf_name}")

            if execution_name:
                set_active_execution(execution_name, wf_name)
            return
        except Exception as e:
            if _is_debug():
                log_unique(f"AWFL_DEBUG: Exception during requests.post: {e}")
            log_unique(f"❌ Error calling API execute: {e}")
            return

    # Placeholder for gcloud execution mode; intentionally left unimplemented in initial split
    log_unique("⚠️ WORKFLOW_EXEC_MODE=gcloud not implemented in utils.workflows yet")


__all__ = [
    "trigger_workflow",
]
