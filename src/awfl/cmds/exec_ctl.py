from __future__ import annotations

import requests
from awfl.auth import get_auth_headers
from awfl.utils import log_unique, _get_workflow_env_suffix, _ensure_env_suffix, get_api_origin
from awfl.state import get_active_execution, clear_active_execution


def stop_or_cancel_active() -> bool:
    log_unique("🛑 Attempting to cancel the active workflow execution via API...")
    active = get_active_execution()
    if not active:
        log_unique("ℹ️ No active workflow execution to cancel.")
        return True
    execution_name, _workflow_name = active
    # Accept either full resource name or raw ID; extract the ID if a resource name is present
    if "/executions/" in execution_name:
        exec_id = execution_name.split("/executions/")[-1]
    else:
        exec_id = execution_name

    # Determine workflow name to include; ensure env suffix for server-side resolution
    wf_name = _workflow_name
    suffix = _get_workflow_env_suffix()
    workflow_for_stop = _ensure_env_suffix(wf_name, suffix)

    origin = get_api_origin()
    url = f"{origin}/workflows/exec/stop"
    try:
        headers = {"Content-Type": "application/json"}
        headers.update(get_auth_headers())
    except Exception as e:
        log_unique(f"❌ Auth initialization failed: {e}")
        return True

    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"execId": exec_id, "workflow": workflow_for_stop},
            timeout=20,
        )
        if resp.status_code >= 400:
            # Try to show JSON error if available
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            log_unique(f"❌ Stop request failed ({resp.status_code}): {err_body}")
        else:
            log_unique(
                f"🛑 Stop requested for execution: {execution_name} (execId={exec_id}, workflow={workflow_for_stop})"
            )
            # Clear immediately for responsiveness
            clear_active_execution()
    except Exception as e:
        log_unique(f"❌ Error calling stop endpoint: {e}")

    return True
