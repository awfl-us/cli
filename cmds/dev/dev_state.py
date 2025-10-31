from __future__ import annotations

from typing import Dict

# Ephemeral session state for dev commands
_state: Dict[str, object] = {
    "watcher_task": None,
    "ngrok_proc": None,
    "compose_started_here": False,
    "last_yaml_snapshot": {},
    "last_changed": [],
}


def get_state() -> Dict[str, object]:
    return _state


def set_state(**kwargs) -> None:
    _state.update(kwargs)


__all__ = [
    "get_state",
    "set_state",
]