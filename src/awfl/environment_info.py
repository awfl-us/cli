"""Public, non-secret capability inventory for the existing AWFL GKE consumer.

This snapshot is sent with the existing project heartbeat. Never read token
contents, return environment-variable values containing credentials, or
include arbitrary paths supplied by callers.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import shutil

GOOGLE_FILE_ENV = "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE"
GITHUB_FILE_ENV = "AWFL_GITHUB_TOKEN_FILE"
GOOGLE_FILE = "/tmp/awfl-google-access-token"
GITHUB_FILE = "/tmp/awfl-github-token"


def _has_token_file(value: str | None) -> bool:
    if not value:
        return False
    try:
        path = Path(value)
        return path.is_file() and path.stat().st_size > 0
    except (OSError, ValueError):
        return False


def _known_token_file(value: str | None, expected: str) -> str | None:
    """Only disclose an explicitly documented AWFL path, never an arbitrary path."""
    return expected if value == expected and _has_token_file(value) else None


def describe_environment() -> dict:
    google_file = os.getenv(GOOGLE_FILE_ENV)
    github_file = os.getenv(GITHUB_FILE_ENV)
    google_present = _has_token_file(google_file) or bool(os.getenv("CLOUDSDK_AUTH_ACCESS_TOKEN"))
    github_present = _has_token_file(github_file) or bool(os.getenv("GITHUB_TOKEN"))
    refresh = bool(os.getenv("AWFL_CREDENTIAL_REFRESH_CAPABILITY"))
    project = os.getenv("CLOUDSDK_CORE_PROJECT", "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", project):
        project = None

    gcloud_available = bool(shutil.which("gcloud") or Path("/opt/google-cloud-sdk/bin/gcloud").is_file())
    git_available = bool(shutil.which("git"))
    playwright_available = importlib.util.find_spec("playwright") is not None
    return {
        "cwd": os.getenv("AWFL_CONSUMER_WORKSPACE") or os.getcwd(),
        "runtime": "shared_gke_python_consumer",
        "credentials": {
            "google_cloud": {
                "available": google_present,
                "token_file_env": GOOGLE_FILE_ENV,
                "token_file": _known_token_file(google_file, GOOGLE_FILE),
                "project_env": "CLOUDSDK_CORE_PROJECT",
                "project": project,
                "refresh_configured": bool(refresh and google_file),
                "cli_available": gcloud_available,
            },
            "github": {
                "available": github_present,
                "token_file_env": GITHUB_FILE_ENV,
                "token_file": _known_token_file(github_file, GITHUB_FILE),
                "refresh_configured": bool(refresh and github_file),
                "git_credential_helper": bool(github_file and git_available),
                "cli_available": git_available,
            },
        },
        "tools": {
            "git": git_available,
            "gcloud": gcloud_available,
            "python": bool(shutil.which("python") or shutil.which("python3")),
            "node": bool(shutil.which("node")),
            "chromium_playwright": playwright_available,
            "java": bool(shutil.which("java")),
            "sbt": bool(shutil.which("sbt")),
        },
    }
