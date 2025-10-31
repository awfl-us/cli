from __future__ import annotations

from pathlib import Path
from typing import List

from utils import log_unique

from ..core import discover_paths, compose_logs


def logs_cmd(args: List[str]) -> bool:
    follow = "--follow" in args or "-f" in args
    paths = discover_paths()
    if paths.compose_file and Path(paths.compose_file).exists():
        compose_logs(paths.compose_file, follow=follow)
    else:
        log_unique("ℹ️ No compose file discovered. Use --compose-file or set AWFL_COMPOSE_FILE.")
    return True


__all__ = ["logs_cmd"]
