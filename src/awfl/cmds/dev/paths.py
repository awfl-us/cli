from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any


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


def _detect_scala_watch_dir(workflows_dir: Path) -> Path:
    """Detect the best Scala source directory to watch.

    Strategy:
    - Base: <workflows_dir>/src/main/scala
    - If <base>/workflows exists, prefer it.
    - Else search under <base> for any directory named 'workflows' and pick the shallowest.
    - If none found, fall back to <base> itself.
    """
    base = workflows_dir / "src" / "main" / "scala"
    try:
        if (base / "workflows").exists():
            return base / "workflows"
        # Find all candidate 'workflows' directories under base
        cand = None
        min_depth = 10**9
        if base.exists():
            for d in base.rglob("workflows"):
                try:
                    if d.is_dir():
                        depth = len(d.relative_to(base).parts)
                        if depth < min_depth:
                            min_depth = depth
                            cand = d
                except Exception:
                    # Skip paths that cannot be relativized
                    continue
        return cand or base
    except Exception:
        return base


def discover_paths(cfg: Dict[str, Any], root: Optional[str] = None) -> DevPaths:
    compose_file_cfg = cfg.get("compose_file")
    workflows_dir_cfg = cfg.get("workflows_dir")

    root_dir = root or _git_root()
    env_workflows = os.getenv("AWFL_WORKFLOWS_DIR")
    workflows_dir = env_workflows or workflows_dir_cfg or str(Path(root_dir))

    compose_env = os.getenv("AWFL_COMPOSE_FILE")
    # Prefer env override if it exists, otherwise search common locations.
    # Search order: repo root docker-compose, workflows docker-compose
    compose_candidates = [
        Path(root_dir) / "docker-compose.yml",
        Path(root_dir) / "docker-compose.yaml",
        Path(workflows_dir) / "docker-compose.yml",
        Path(workflows_dir) / "docker-compose.yaml",
    ]
    compose_file: Optional[str] = compose_file_cfg
    if compose_env:
        compose_file = compose_env if Path(compose_env).exists() else None
    if compose_file is None:
        for c in compose_candidates:
            if c and c.exists():
                compose_file = str(c)
                break

    yaml_gens_dir = str(Path(workflows_dir) / "yaml_gens")
    scala_watch_dir = _detect_scala_watch_dir(Path(workflows_dir))

    return DevPaths(
        repo_root=root_dir,
        workflows_dir=workflows_dir,
        compose_file=compose_file,
        yaml_gens_dir=yaml_gens_dir,
        scala_src_dir=str(scala_watch_dir),
    )


__all__ = [
    "DevPaths",
    "discover_paths",
]