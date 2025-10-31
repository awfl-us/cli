from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DevPaths:
    repo_root: str
    workflows_dir: str
    compose_file: Optional[str]
    yaml_gens_dir: str
    scala_src_dir: str


def _git_root(cwd: Optional[str] = None) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return cwd or os.getcwd()


def discover_paths(root: Optional[str] = None) -> DevPaths:
    root_dir = root or _git_root()
    env_workflows = os.getenv("AWFL_WORKFLOWS_DIR")
    workflows_dir = env_workflows or str(Path(root_dir) / "workflows")

    compose_env = os.getenv("AWFL_COMPOSE_FILE")
    # Prefer env override if it exists, otherwise search common locations.
    # Search order: repo root docker-compose, workflows docker-compose
    compose_candidates = [
        Path(root_dir) / "docker-compose.yml",
        Path(root_dir) / "docker-compose.yaml",
        Path(workflows_dir) / "docker-compose.yml",
        Path(workflows_dir) / "docker-compose.yaml",
    ]
    compose_file: Optional[str] = None
    if compose_env:
        compose_file = compose_env if Path(compose_env).exists() else None
    if compose_file is None:
        for c in compose_candidates:
            if c.exists():
                compose_file = str(c)
                break

    yaml_gens_dir = str(Path(workflows_dir) / "yaml_gens")
    scala_src_dir = str(Path(workflows_dir) / "src" / "main" / "scala" / "workflows")

    return DevPaths(
        repo_root=root_dir,
        workflows_dir=workflows_dir,
        compose_file=compose_file,
        yaml_gens_dir=yaml_gens_dir,
        scala_src_dir=scala_src_dir,
    )


__all__ = [
    "DevPaths",
    "discover_paths",
]