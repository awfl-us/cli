import json
import os
from typing import Optional

LOCKS_DIR = os.path.expanduser("~/.awfl/locks")


def _project_lock_path(project_id: str) -> str:
    os.makedirs(LOCKS_DIR, exist_ok=True)
    return os.path.join(LOCKS_DIR, f"project_sse_{project_id}.lock")


def _pid_is_alive(pid: int) -> bool:
    try:
        # Works on Unix; on Windows may raise AttributeError; best-effort
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def try_acquire_project_leader(project_id: str) -> bool:
    """Attempt to acquire a simple project-wide leader lock using a lock file.

    Returns True if acquired; False if another live process holds it.
    """
    path = _project_lock_path(project_id)
    # If a lock exists, check if the process is alive; if not, remove stale lock
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid_val = data.get("pid")
            pid = int(pid_val) if isinstance(pid_val, (int, str)) else None
        except Exception:
            pid = None
        if pid and _pid_is_alive(int(pid)):
            return False
        try:
            os.remove(path)
        except Exception:
            # If cannot remove, assume not acquired
            return False
    # Try to create atomically
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid()}, f)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def release_project_leader(project_id: str) -> None:
    try:
        os.remove(_project_lock_path(project_id))
    except Exception:
        pass
