from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Dict, Any

from awfl.utils import log_unique

from ..core import discover_paths, compose_down, stop_ngrok, compose_status
from ..core import get_state, set_state, load_dev_config


def stop_dev(args: List[str]) -> bool:
    state = get_state()

    no_ngrok = "--no-ngrok" in args
    no_compose = "--no-compose" in args

    # Stop Scala watcher task if running
    task = state.get("watcher_task")
    if task and not task.done():
        cancel = getattr(task, "_awfl_cancel", None)
        if callable(cancel):
            cancel()
        task.cancel()
        # If we're not inside a running event loop, we can wait for cancellation
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                try:
                    loop.run_until_complete(asyncio.shield(task))
                except Exception:
                    pass
        except RuntimeError:
            # No event loop; nothing further to await
            pass
        set_state(watcher_task=None)
        log_unique("🛑 Watcher stopped.")

    # Bring down docker compose (replicate dev.sh behavior: always down if compose file exists)
    cfg: Dict[str, Any] = load_dev_config() or {}
    paths = discover_paths(cfg)
    if paths.compose_file and not no_compose and Path(paths.compose_file).exists():
        compose_down(paths.compose_file)
        set_state(compose_started_here=False)
        log_unique("🧹 docker compose down.")

    # Stop ngrok if we started it in this session
    proc = state.get("ngrok_proc")
    if proc and not no_ngrok:
        try:
            stop_ngrok(proc)
        finally:
            set_state(ngrok_proc=None)
        log_unique("🧹 ngrok stopped.")

    log_unique("✅ dev stop complete.")
    return True


__all__ = ["stop_dev"]
