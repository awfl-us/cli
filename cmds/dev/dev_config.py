from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from utils import log_unique


def _config_dir() -> Path:
    return Path(os.path.expanduser("~/.awfl"))


def _config_path() -> Path:
    return _config_dir() / "dev_config.json"


def load_dev_config() -> Dict[str, Any]:
    try:
        p = _config_path()
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_dev_config(cfg: Dict[str, Any]) -> None:
    try:
        d = _config_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = _config_path()
        with p.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
        log_unique(f"📝 Saved dev config to {p}")
    except Exception as e:
        log_unique(f"⚠️ Failed to save dev config: {e}")


__all__ = [
    "load_dev_config",
    "save_dev_config",
]