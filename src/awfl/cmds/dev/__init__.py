from __future__ import annotations

# Re-export primary entrypoint for router compatibility
from .commands import handle_dev_command

__all__ = [
    "handle_dev_command",
]
