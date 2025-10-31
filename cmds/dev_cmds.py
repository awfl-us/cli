from __future__ import annotations

import warnings

# Compatibility shim: legacy imports continue to work
# New implementation lives under cmds/dev/
from .dev.commands import handle_dev_command
from .dev.subcommands import (
    start_dev,
    stop_dev,
    logs_cmd,
    generate_yamls_cmd,
    deploy_workflow_cmd,
    status_cmd,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.warn(
    "dev_cmds.py is deprecated; use cmds.dev.commands instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "handle_dev_command",
    "start_dev",
    "stop_dev",
    "logs_cmd",
    "generate_yamls_cmd",
    "deploy_workflow_cmd",
    "status_cmd",
]
