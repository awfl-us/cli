from __future__ import annotations

import os
from typing import Optional

from awfl.state import (
    DEFAULT_WORKFLOW,
    get_active_workflow,
    normalize_workflow,
)

# Try to use repo logging if available; fall back to no-op to stay quiet.
try:  # pragma: no cover - best-effort import
    from awfl.utils import log_unique as _log_unique  # type: ignore
except Exception:  # pragma: no cover - keep startup resilient
    def _log_unique(_: str) -> None:  # type: ignore
        return


def _safe_normalize(name: Optional[str]) -> str:
    """Normalize a workflow name with layered fallbacks.

    - If name is falsy/blank, fall back to DEFAULT_WORKFLOW.
    - Any exception while normalizing is swallowed and we fall back to
      DEFAULT_WORKFLOW, and finally to a raw DEFAULT_WORKFLOW string if
      normalization itself raises.
    """
    base = (name or "").strip() or DEFAULT_WORKFLOW
    try:
        return normalize_workflow(base)
    except Exception:  # Be robust on unexpected issues
        try:
            return normalize_workflow(DEFAULT_WORKFLOW)
        except Exception:
            # Absolute last resort: return the constant as-is
            return DEFAULT_WORKFLOW


def compute_session_workflow_name() -> str:
    """Resolve the workflow identity for this session (used for SSE filtering).

    Precedence (first non-empty wins):
    1) ASSISTANT_WORKFLOW env var (normalized)
    2) state.get_active_workflow() (normalized)
    3) DEFAULT_WORKFLOW (normalized), with raw constant as a last resort

    Notes:
    - Quiet and resilient: never raises on normal paths. Any internal error
      results in a safe fallback to DEFAULT_WORKFLOW.
    - Whitespace-only ASSISTANT_WORKFLOW is ignored.
    - Always returns a string.
    """
    try:
        # 1) Explicit override via env, if present and non-blank
        override = os.environ.get("ASSISTANT_WORKFLOW", "")
        if override and override.strip():
            return _safe_normalize(override)

        # 2) Active workflow from state, may be None/empty
        try:
            active = get_active_workflow()
        except Exception as e:  # keep startup resilient
            _log_unique(f"session.workflow: get_active_workflow failed; using default ({e.__class__.__name__})")
            active = None
        return _safe_normalize(active)

    except Exception as e:  # Belt-and-suspenders: any unexpected error
        _log_unique(
            f"session.workflow: unexpected error computing workflow; using default ({e.__class__.__name__})"
        )
        return _safe_normalize(None)
