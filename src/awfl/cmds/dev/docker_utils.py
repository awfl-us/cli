from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from awfl.utils import log_unique


# ----------------------- Docker compose helpers -----------------------

def compose_up(compose_file: str) -> bool:
    if not shutil.which("docker"):
        log_unique("⚠️ docker is not installed or not in PATH.")
        return False
    try:
        # Match legacy dev.sh behavior by forcing a build on up
        res = subprocess.run(["docker", "compose", "-f", compose_file, "up", "-d", "--build"], text=True)
        if res.returncode == 0:
            log_unique(f"🐳 docker compose up -d --build (file: {compose_file})")
            return True
        log_unique(f"⚠️ docker compose up failed (code {res.returncode})")
        return False
    except Exception as e:
        log_unique(f"⚠️ docker compose up error: {e}")
        return False


def compose_down(compose_file: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(["docker", "compose", "-f", compose_file, "down"], text=True)
        return res.returncode == 0
    except Exception:
        return False


def compose_logs(compose_file: str, follow: bool) -> None:
    if not shutil.which("docker"):
        log_unique("⚠️ docker is not installed or not in PATH.")
        return
    args = ["docker", "compose", "-f", compose_file, "logs"]
    if follow:
        args.append("-f")
    try:
        subprocess.run(args)
    except KeyboardInterrupt:
        pass


def compose_status(compose_file: Optional[str]) -> str:
    if not compose_file:
        return "unknown"
    if not shutil.which("docker"):
        return "unknown"
    try:
        res = subprocess.run(["docker", "compose", "-f", compose_file, "ps"], capture_output=True, text=True)
        if res.returncode != 0:
            return "unknown"
        if "Up" in res.stdout:
            return "up"
        if "Exit" in res.stdout or "Exited" in res.stdout:
            return "down"
        return "unknown"
    except Exception:
        return "unknown"


def create_default_compose(repo_root: str, js_host_port: int = 5050, firestore_host_port: int = 8085, ui_host_port: int = 4000) -> Optional[str]:
    """Create a default docker-compose.yml in the repo root if it doesn't exist.

    Returns the path to the created/existing file, or None on failure.
    """
    try:
        target = Path(repo_root) / "docker-compose.yml"
        if target.exists():
            return str(target)

        compose = f"""version: '3.8'

services:
  js-server:
    build:
      context: ./functions
      dockerfile: Dockerfile
    ports:
      - "{js_host_port}:5050"  # Unified port for API + static
    environment:
      - GCP_PROJECT=topaigents
      - NODE_ENV=development
      - GOOGLE_MAPS_API_KEY=${{GOOGLE_MAPS_API_KEY}}
      - OPENAI_API_KEY=${{OPENAI_API_KEY}}
      - GOOGLE_APPLICATION_CREDENTIALS=/app/functions/serviceAccountKey.json
      - PYTHON_API=http://python-server:8080
      - FIRESTORE_EMULATOR_HOST=firestore-emulator:8085
      - WORKFLOW_BASE_URL=http://127.0.0.1:{js_host_port}/jobs
      - WORKFLOW_ENV=Dev
      - CHROMIUM_PATH=/usr/bin/headless-chromium
      - BASE_URL=${{BASE_URL}}
    volumes:
      - ./serviceAccountKey.json:/app/functions/serviceAccountKey.json
      - ./functions:/app/functions
      - ./web:/app/web
      - /app/functions/node_modules
      - ./web/js/config/local.js:/app/web/js/config.js
    depends_on:
      - firestore-emulator
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:5050/api/healthz"]
      interval: 5s
      timeout: 2s
      retries: 20

  firestore-emulator:
    build:
      context: .
    dockerfile: Dockerfile.firebase-emulator
    command: >
      firebase emulators:start --only firestore --import=/app/.emulator_data/firestore --export-on-exit
    ports:
      - "{firestore_host_port}:8085"
      - "{ui_host_port}:4000"
    volumes:
      - ./.firebaserc:/app/.firebaserc
      - ./firebase.json:/app/firebase.json
      - ./.emulator_data:/app/.emulator_data
    restart: unless-stopped

  nginx-router:
    image: nginx:stable-alpine
    ports:
      - "8081:8081" # Nginx router accessible from outside
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      js-server:
        condition: service_healthy
    restart: unless-stopped
"""
        target.write_text(compose, encoding="utf-8")
        log_unique(f"🆕 Created default docker-compose at {target}")
        return str(target)
    except Exception as e:
        log_unique(f"⚠️ Failed to create docker-compose.yml: {e}")
        return None


def ensure_nginx_conf(repo_root: str) -> Optional[str]:
    """Create a default nginx.conf if missing."""
    try:
        target = Path(repo_root) / "nginx.conf"
        if target.exists():
            return str(target)
        content = """events {}

http {
    server {
        listen 8081;

        location / {
            proxy_pass http://js-server:5050/;  # Firebase Functions emulator
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection 'upgrade';
            proxy_set_header Host $host;
            proxy_cache_bypass $http_upgrade;

            proxy_read_timeout 600s;
            proxy_connect_timeout 600s;
        }
    }
}
"""
        target.write_text(content, encoding="utf-8")
        log_unique(f"🆕 Created default nginx.conf at {target}")
        return str(target)
    except Exception as e:
        log_unique(f"⚠️ Failed to create nginx.conf: {e}")
        return None


__all__ = [
    "compose_up",
    "compose_down",
    "compose_logs",
    "compose_status",
    "create_default_compose",
    "ensure_nginx_conf",
]