from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Dict, Any

from awfl.utils import log_unique

from ..core import discover_paths, compose_down, stop_ngrok, compose_status
from ..core import get_state, set_state, load_dev_config


def _cancel_task(task, label: str) -> None:
    if not task or task.done():
        return
    cancel = getattr(task, "_awfl_cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass
    try:
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
    finally:
        log_unique(f"🛑 {label} stopped.")


def stop_dev(args: List[str]) -> bool:
    state = get_state()

    no_ngrok = "--no-ngrok" in args
    no_compose = "--no-compose" in args

    # Stop Scala/YAML watcher task if running
    _cancel_task(state.get("watcher_task"), "Watcher")
    set_state(watcher_task=None)

    # Stop files watcher task if running
    _cancel_task(state.get("scripts_watcher_task"), "Files watcher")
    set_state(scripts_watcher_task=None)

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
