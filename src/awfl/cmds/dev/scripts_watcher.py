from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

from awfl.utils import log_unique
from .paths import DevPaths
from .yaml_ops import _env_suffix


def _bucket_name(project: Optional[str]) -> str:
    """Compute the files bucket name.

    If a GCP project is provided, use per-project scoped bucket with a lowercase env suffix:
      <project>-workflow-files<env_suffix_lower>
    Otherwise, fall back to the legacy name:
      workflow-files<EnvSuffix>
    """
    if project and str(project).strip():
        return f"{project}-workflow-files{_env_suffix().lower()}"
    return f"workflow-files{_env_suffix()}"


def _gs_uri(rel_path: str, bucket: str) -> str:
    # Normalize to posix for GCS object name
    rel = rel_path.strip().lstrip("/\\").replace("\\", "/")
    return f"gs://{bucket}/{rel}" if rel else f"gs://{bucket}"


def _have_gsutil() -> bool:
    return shutil.which("gsutil") is not None


def _run(cmd: List[str]) -> Tuple[int, str, str]:
    shell_cmd = " ".join(shlex.quote(c) for c in cmd)
    res = subprocess.run(["bash", "-lc", shell_cmd], capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def _upload_file(files_root: Path, file_path: str, bucket: str) -> None:
    if not _have_gsutil():
        log_unique("⚠️ gsutil is not installed; cannot upload files.")
        return
    try:
        rel = str(Path(file_path).resolve().relative_to(files_root.resolve()))
    except Exception:
        # File not under files_root (e.g., move out); skip
        return
    src = str(Path(file_path))
    dst = _gs_uri(rel, bucket)
    # Overwrite on modify: do not use -n
    code, out, err = _run(["gsutil", "-m", "cp", src, dst])
    if code == 0:
        log_unique(f"☁️ Uploaded {rel} -> {dst}")
    else:
        tail = (err or out or "").strip().splitlines()[-20:]
        log_unique(f"❌ gsutil cp failed for {rel}:\n" + "\n".join(tail))


def _remove_path(rel_path: str, is_dir: bool, bucket: str) -> None:
    if not _have_gsutil():
        log_unique("⚠️ gsutil is not installed; cannot remove files from bucket.")
        return
    uri = _gs_uri(rel_path, bucket)
    args = ["gsutil", "-m", "rm"]
    if is_dir:
        args.append("-r")
    args.append(uri)
    code, out, err = _run(args)
    if code == 0:
        what = f"prefix {rel_path}/" if is_dir else rel_path
        log_unique(f"🗑️ Removed {what} from gs://{bucket}")
    else:
        tail = (err or out or "").strip().splitlines()[-20:]
        log_unique(f"⚠️ gsutil rm failed for {rel_path}:\n" + "\n".join(tail))


async def _watch_scripts_loop(paths: DevPaths, debounce_ms: int, stop_event: asyncio.Event, project: Optional[str]):
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    files_root = Path(paths.workflows_dir) / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    bucket = _bucket_name(project)
    if not (project and str(project).strip()):
        log_unique(f"⚠️ PROJECT not set; using legacy bucket name: {bucket}")

    queue: asyncio.Queue = asyncio.Queue()

    class Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.is_directory:
                return
            try:
                queue.put_nowait(("mod", str(event.src_path), None, False))
            except Exception:
                pass

        def on_created(self, event):
            if event.is_directory:
                return
            try:
                queue.put_nowait(("new", str(event.src_path), None, False))
            except Exception:
                pass

        def on_moved(self, event):
            try:
                queue.put_nowait(("mv", str(event.src_path), str(getattr(event, "dest_path", "")), bool(getattr(event, "is_directory", False))))
            except Exception:
                pass

        def on_deleted(self, event):
            try:
                queue.put_nowait(("del", str(event.src_path), None, bool(getattr(event, "is_directory", False))))
            except Exception:
                pass

    observer = Observer()
    log_unique(f"👀 Watching files: {files_root} -> gs://{bucket}")
    observer.schedule(Handler(), str(files_root), recursive=True)
    observer.start()

    try:
        pending_at: Optional[float] = None
        # Track rel paths to upload and delete
        to_upload: Set[str] = set()
        to_delete_files: Set[str] = set()
        to_delete_dirs: Set[str] = set()

        def _rel_under_files(p: str) -> Optional[str]:
            try:
                return str(Path(p).resolve().relative_to(files_root.resolve()))
            except Exception:
                return None

        while not stop_event.is_set():
            try:
                kind, src, dst, is_dir = await asyncio.wait_for(queue.get(), timeout=0.5)
                if kind in ("mod", "new"):
                    rel = _rel_under_files(src)
                    if rel:
                        to_upload.add(rel)
                        to_delete_files.discard(rel)
                    pending_at = time.time()
                elif kind == "mv":
                    rel_src = _rel_under_files(src)
                    rel_dst = _rel_under_files(dst or "") if dst else None
                    if rel_src and not rel_dst:
                        # moved out -> delete src
                        if is_dir:
                            to_delete_dirs.add(rel_src)
                        else:
                            to_delete_files.add(rel_src)
                        to_upload.discard(rel_src)
                    if rel_dst:
                        # moved in or within -> upload dest (for dirs, we will rely on subsequent create/modify events for files)
                        if not is_dir:
                            to_upload.add(rel_dst)
                            to_delete_files.discard(rel_dst)
                    pending_at = time.time()
                elif kind == "del":
                    rel = _rel_under_files(src)
                    if rel:
                        if is_dir:
                            to_delete_dirs.add(rel)
                        else:
                            to_delete_files.add(rel)
                        to_upload.discard(rel)
                    pending_at = time.time()
            except asyncio.TimeoutError:
                pass

            if pending_at is not None and (time.time() - pending_at) * 1000 >= debounce_ms:
                uploads = sorted(to_upload)
                dels_files = sorted(to_delete_files)
                dels_dirs = sorted(to_delete_dirs)
                to_upload.clear()
                to_delete_files.clear()
                to_delete_dirs.clear()
                pending_at = None

                if not uploads and not dels_files and not dels_dirs:
                    continue

                # Apply deletes before uploads to avoid conflicts
                for d in dels_dirs:
                    _remove_path(d, is_dir=True, bucket=bucket)
                for f in dels_files:
                    _remove_path(f, is_dir=False, bucket=bucket)
                for u in uploads:
                    _upload_file(files_root, str(files_root / u), bucket=bucket)
    finally:
        observer.stop()
        observer.join()


def watch_scripts(paths: DevPaths, *, debounce_ms: int = 500, project: Optional[str] = None) -> asyncio.Task:
    stop_event = asyncio.Event()

    async def runner():
        await _watch_scripts_loop(paths, debounce_ms, stop_event, project)

    task = asyncio.create_task(runner(), name="awfl-dev-files-watcher")

    def _cancel(_=None):
        stop_event.set()

    task.add_done_callback(lambda _t: stop_event.set())
    setattr(task, "_awfl_cancel", _cancel)
    return task


__all__ = [
    "watch_scripts",
]
