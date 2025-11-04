from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from awfl.utils import log_unique
from awfl.auth import get_project_id
from awfl.events.workspace import repo_remote, _derive_project_name


def _config_dir() -> Path:
    return Path(os.path.expanduser("~/.awfl"))


def _config_path() -> Path | None:
    repo = _derive_project_name(repo_remote())
    if repo:
      return _config_dir() / Path(repo) / f"dev_config.json"


def load_dev_config() -> Dict[str, Any]:
    try:
        p = _config_path()
        if p and p.exists():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_dev_config(cfg: Dict[str, Any]) -> None:
    try:
        p = _config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
        log_unique(f"📝 Saved dev config to {p}")
    except Exception as e:
        log_unique(f"⚠️ Failed to save dev config: {e}")


def resolve_location_project() -> Tuple[str, str]:
    """Resolve GCP location and project.

    Precedence:
    1) dev config (if available): location, project
    2) environment: AWFL_GCLOUD_LOCATION, PROJECT | GOOGLE_CLOUD_PROJECT | GCLOUD_PROJECT
    3) defaults: us-central1, topaigents
    """
    try:
        cfg = load_dev_config() or {}
    except Exception:
        cfg = {}

    location = cfg.get("location") or os.getenv("AWFL_GCLOUD_LOCATION") or "us-central1"
    project = (
        cfg.get("project")
        or os.getenv("PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or "topaigents"
    )
    return str(location), str(project)


__all__ = [
    "load_dev_config",
    "save_dev_config",
    "resolve_location_project",
]