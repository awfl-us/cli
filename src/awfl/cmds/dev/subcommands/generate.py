from __future__ import annotations

from typing import List, Dict, Any

from awfl.utils import log_unique

from ..core import discover_paths, generate_yamls, _short_display, load_dev_config


def generate_yamls_cmd(args: List[str]) -> bool:
    cfg: Dict[str, Any] = load_dev_config() or {}
    paths = discover_paths(cfg)
    changed = generate_yamls(paths)
    if changed:
        log_unique("Changed YAMLs:\n- " + "\n- ".join(_short_display(paths, c) for c in changed))
    else:
        log_unique("No YAMLs changed.")
    return True


__all__ = ["generate_yamls_cmd"]
