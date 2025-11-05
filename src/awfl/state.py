from __future__ import annotations

from typing import Optional, Tuple

# Default workflow used when none is selected
DEFAULT_WORKFLOW = "codebase-ProjectManager"

# Active workflow (normalized)
_active_workflow: Optional[str] = None

# Active execution (execution resource name, workflow name)
_active_execution: Optional[Tuple[str, str]] = None

# Process-local workflow environment suffix (e.g., "Dev" or "")
_workflow_env_suffix: Optional[str] = None


def normalize_workflow(name: str | None) -> str:
    """Normalize workflow identifier to dashed form and strip prefixes.
    - Strips leading "workflows." if present
    - Replaces dots with dashes
    - Falls back to DEFAULT_WORKFLOW when name is falsy
    """
    if not name:
        return DEFAULT_WORKFLOW
    n = name.strip()
    if n.startswith("workflows."):
        n = n[len("workflows."):]
    n = n.replace(".", "-")
    return n


def get_active_workflow() -> Optional[str]:
    return _active_workflow


def set_active_workflow(name: str | None) -> None:
    global _active_workflow
    _active_workflow = normalize_workflow(name) if name else DEFAULT_WORKFLOW


def set_active_execution(execution_name: str, workflow_name: str) -> None:
    global _active_execution
    _active_execution = (execution_name, workflow_name)


def get_active_execution() -> Optional[Tuple[str, str]]:
    return _active_execution


def clear_active_execution() -> None:
    global _active_execution
    _active_execution = None


def set_workflow_env_suffix(suffix: Optional[str]) -> None:
    """Set the per-process workflow environment suffix.
    Use "Dev" for dev mode or "" (empty string) for prod mode. None means unset.
    """
    global _workflow_env_suffix
    _workflow_env_suffix = suffix


def get_workflow_env_suffix() -> Optional[str]:
    """Return the per-process workflow environment suffix if set, else None."""
    return _workflow_env_suffix


__all__ = [
    "DEFAULT_WORKFLOW",
    "normalize_workflow",
    "get_active_workflow",
    "set_active_workflow",
    "set_active_execution",
    "get_active_execution",
    "clear_active_execution",
    "set_workflow_env_suffix",
    "get_workflow_env_suffix",
]
