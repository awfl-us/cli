from __future__ import annotations

import asyncio
import os
import time
from typing import Optional, Set, List

from awfl.utils import log_unique
from .paths import DevPaths
from .yaml_ops import generate_for_classes, deploy_workflow, _class_path_from_scala_file
from .dev_config import resolve_location_project


async def _watch_loop(paths: DevPaths, auto_deploy: bool, debounce_ms: int, stop_event: asyncio.Event):
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    queue: asyncio.Queue = asyncio.Queue()

    class Handler(FileSystemEventHandler):
        def _enqueue(self, src_path: str):
            if not src_path.endswith(".scala"):
                return
            try:
                queue.put_nowait(src_path)
            except Exception:
                pass

        def on_modified(self, event):
            if event.is_directory:
                return
            self._enqueue(str(event.src_path))

        on_created = on_modified
        on_moved = on_modified

    observer = Observer()
    log_unique(f"👀 Watching for Scala changes in: {paths.scala_src_dir}")
    observer.schedule(Handler(), paths.scala_src_dir, recursive=True)
    observer.start()

    try:
        pending_at: Optional[float] = None
        changed_files: Set[str] = set()
        while not stop_event.is_set():
            try:
                src_path = await asyncio.wait_for(queue.get(), timeout=0.5)
                changed_files.add(src_path)
                pending_at = time.time()
            except asyncio.TimeoutError:
                pass

            if pending_at is not None and (time.time() - pending_at) * 1000 >= debounce_ms:
                files: List[str] = sorted(changed_files)
                changed_files.clear()
                pending_at = None

                # Map files -> class dot-paths
                classes: List[str] = []
                for f in files:
                    cp = _class_path_from_scala_file(paths, f)
                    if cp:
                        classes.append(cp)
                if not classes:
                    continue

                log_unique("🌀 Detected Scala changes:\n- " + "\n- ".join(files))
                log_unique("🎯 Regenerating for classes:\n- " + "\n- ".join(classes))

                changed = generate_for_classes(paths, classes)
                if auto_deploy and changed:
                    loc, proj = resolve_location_project()
                    for y in changed:
                        deploy_workflow(y, loc, proj)
    finally:
        observer.stop()
        observer.join()


def watch_workflows(paths: DevPaths, *, auto_deploy: bool = True, debounce_ms: int = 600) -> asyncio.Task:
    stop_event = asyncio.Event()

    async def runner():
        await _watch_loop(paths, auto_deploy, debounce_ms, stop_event)

    task = asyncio.create_task(runner(), name="awfl-dev-watcher")

    def _cancel(_=None):
        stop_event.set()

    task.add_done_callback(lambda _t: stop_event.set())
    setattr(task, "_awfl_cancel", _cancel)
    return task


__all__ = [
    "watch_workflows",
]