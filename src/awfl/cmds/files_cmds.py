from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from awfl.utils import log_unique

# Reuse helpers and config from dev modules
from awfl.cmds.dev.core import discover_paths, load_dev_config
from awfl.cmds.dev.scripts_watcher import _bucket_name, _have_gsutil, _run


def upload_files_cmd(args: List[str]) -> bool:
    """Upload/sync all files under workflows/files/ to the configured GCS bucket.

    Usage: upload files [--delete]
      --delete  Also remove remote objects that don't exist locally (gsutil rsync -d)
    """
    delete_remote = "--delete" in args or "-d" in args

    cfg = load_dev_config() or {}
    # Determine project precedence: env > saved config > None
    project: Optional[str] = os.getenv("PROJECT") or cfg.get("project")

    paths = discover_paths(cfg)
    files_root = Path(paths.workflows_dir) / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    if not _have_gsutil():
        log_unique("❌ gsutil is not installed; cannot upload files.")
        return True

    bucket = _bucket_name(project)
    if not (project and str(project).strip()):
        log_unique(f"⚠️ PROJECT not set; using legacy bucket name: {bucket}")

    # Use gsutil rsync for efficient one-shot synchronization
    dst = f"gs://{bucket}"
    args = ["gsutil", "-m", "rsync", "-r"]
    if delete_remote:
        args.append("-d")
    args.extend([str(files_root), dst])

    code, out, err = _run(args)
    if code == 0:
        log_unique(f"✅ Files synced: {files_root} -> {dst}{' (with delete)' if delete_remote else ''}")
    else:
        tail = (err or out or "").strip().splitlines()[-50:]
        log_unique("❌ gsutil rsync failed:\n" + "\n".join(tail))
    return True


__all__ = ["upload_files_cmd"]
