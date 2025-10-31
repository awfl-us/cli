from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

from awfl.utils import log_unique


def _get_ngrok_existing_url() -> Optional[str]:
    try:
        import urllib.request

        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tunnels = data.get("tunnels", [])
            for t in tunnels:
                pub = t.get("public_url")
                if pub and pub.startswith("https://"):
                    return pub
    except Exception:
        return None
    return None


def start_ngrok(port: int):
    existing = _get_ngrok_existing_url()
    if existing:
        log_unique(f"🔗 ngrok tunnel detected: {existing}")
        return None
    if not shutil.which("ngrok"):
        log_unique("ℹ️ ngrok not found in PATH; skipping tunnel start.")
        return None
    try:
        proc = subprocess.Popen(["ngrok", "http", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_unique(f"🚇 Starting ngrok http {port} (background)")
        return proc
    except Exception as e:
        log_unique(f"⚠️ Failed to start ngrok: {e}")
        return None


def stop_ngrok(proc, port: Optional[int] = None) -> None:
    try:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
    except Exception:
        pass
    if port is not None:
        try:
            subprocess.run(["pkill", "-f", f"ngrok http {port}"], capture_output=True)
        except Exception:
            pass


__all__ = [
    "_get_ngrok_existing_url",
    "start_ngrok",
    "stop_ngrok",
]