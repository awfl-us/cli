from __future__ import annotations

import os
from typing import List, Dict, Any

from awfl.utils import log_unique

from ..core import discover_paths, compose_status, get_state, _short_display, _env_suffix
from ..core import _get_ngrok_existing_url, load_dev_config


def status_cmd(args: List[str]) -> bool:
    cfg: Dict[str, Any] = load_dev_config() or {}
    paths = discover_paths(cfg)

    tunnel = _get_ngrok_existing_url()
    comp = compose_status(paths.compose_file)
    state = get_state()
    watch = "running" if state.get("watcher_task") and not state.get("watcher_task").done() else "stopped"

    last = state.get("last_changed") or []
    last_disp = ("\n- " + "\n- ".join(_short_display(paths, c) for c in last)) if last else " (none)"

    log_unique(
        "Dev status:\n"
        f"- repo: {paths.repo_root}\n"
        f"- workflows: {paths.workflows_dir}\n"
        f"- yaml_gens: {paths.yaml_gens_dir}\n"
        f"- ngrok: {tunnel or 'none'}\n"
        f"- compose: {paths.compose_file or 'none'} ({comp})\n"
        f"- watcher: {watch}\n"
        f"- last changed YAMLs:{last_disp}\n"
        f"- default deploy: {os.getenv('PROJECT', 'topaigents')}/{os.getenv('AWFL_GCLOUD_LOCATION', 'us-central1')} (suffix {_env_suffix()})"
    )
    return True


__all__ = ["status_cmd"]
