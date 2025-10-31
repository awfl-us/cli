from __future__ import annotations

from typing import List

from utils import log_unique

from ..core import discover_paths, generate_yamls, _short_display


def generate_yamls_cmd(args: List[str]) -> bool:
    paths = discover_paths()
    changed = generate_yamls(paths)
    if changed:
        log_unique("Changed YAMLs:\n- " + "\n- ".join(_short_display(paths, c) for c in changed))
    else:
        log_unique("No YAMLs changed.")
    return True


__all__ = ["generate_yamls_cmd"]
