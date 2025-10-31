from __future__ import annotations

import os
from pathlib import Path

# Shared constants and small helpers used by command handlers

TOKEN_CACHE_PATH = Path.home() / ".awfl" / "tokens.json"


def get_orig_cwd() -> str:
    """Return the directory awfl was launched from (AWFL_ORIG_CWD) or cwd."""
    return os.environ.get("AWFL_ORIG_CWD", os.getcwd())


def relpath_display(path: str | Path) -> str:
    """Pretty-print a path relative to the original launch directory when possible."""
    try:
        base = Path(get_orig_cwd())
        p = Path(path)
        rel = os.path.relpath(str(p), str(base))
        if not rel.startswith("..") and not os.path.isabs(rel):
            return rel
    except Exception:
        pass
    return str(path)


class wf_utils:
    """Utilities for workflow name handling and environment suffixing."""

    @staticmethod
    def _get_workflow_env_suffix() -> str:
        # Default to Dev until config module introduces richer env handling.
        return "Dev"

    @staticmethod
    def _ensure_env_suffix(name: str, suffix: str) -> str:
        if not suffix:
            return name
        if name.endswith(suffix):
            return name
        return f"{name}{suffix}"
