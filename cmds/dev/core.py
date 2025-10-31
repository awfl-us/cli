from __future__ import annotations

# This module has been split into focused modules for clarity and maintainability.
# It now serves as a thin compatibility layer that re-exports the public API.

from .paths import DevPaths, discover_paths
from .dev_config import load_dev_config, save_dev_config
from .dev_state import get_state, set_state
from .ngrok_utils import start_ngrok, stop_ngrok, _get_ngrok_existing_url
from .docker_utils import (
    compose_up,
    compose_down,
    compose_logs,
    compose_status,
    create_default_compose,
    ensure_nginx_conf,
)
from .yaml_ops import (
    _snapshot_yaml_mtimes,
    generate_yamls,
    _short_display,
    derive_workflow_name,
    _env_suffix,
    deploy_workflow,
)
from .watcher import watch_workflows

__all__ = [
    # paths
    "DevPaths",
    "discover_paths",
    # config
    "load_dev_config",
    "save_dev_config",
    # state
    "get_state",
    "set_state",
    # ngrok
    "start_ngrok",
    "stop_ngrok",
    "_get_ngrok_existing_url",
    # docker
    "compose_up",
    "compose_down",
    "compose_logs",
    "compose_status",
    "create_default_compose",
    "ensure_nginx_conf",
    # yaml ops
    "_snapshot_yaml_mtimes",
    "generate_yamls",
    "_short_display",
    "derive_workflow_name",
    "_env_suffix",
    "deploy_workflow",
    # watcher
    "watch_workflows",
]