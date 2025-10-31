import os
import subprocess
from pathlib import Path
from typing import List

from utils import log_unique
from .common import get_orig_cwd

# Reuse dev core helpers for generation and deployment
try:
    # Local import to avoid heavy deps unless command is invoked
    from .dev.core import discover_paths
    from .dev.yaml_ops import generate_yamls, deploy_workflow  # type: ignore
except Exception:  # pragma: no cover - fall back to touch mode only
    discover_paths = None  # type: ignore
    generate_yamls = None  # type: ignore
    deploy_workflow = None  # type: ignore


def _find_git_root(start: Path) -> Path:
    """Return the git repo root for 'start', or 'start' if not in a repo."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out:
            return Path(out)
    except Exception:
        pass

    # Fallback: walk up to find a .git directory
    cur = start
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return start
        cur = cur.parent


def _gather_scala_sources(repo_root: Path) -> List[Path]:
    scala_dir = repo_root / "workflows" / "src" / "main" / "scala" / "workflows"
    files: List[Path] = []
    if scala_dir.exists():
        files = [f for f in sorted(scala_dir.rglob("*.scala")) if f.is_file()]
    return files


def _list_yaml_files(yaml_root: Path) -> List[Path]:
    files: List[Path] = []
    if not yaml_root.exists():
        return files
    for ext in ("*.yaml", "*.yml"):
        files.extend(sorted(yaml_root.rglob(ext)))
    return [f for f in files if f.is_file()]


def deploy_workflows() -> bool:
    """Rebuild and deploy all workflows in one command.

    Behavior:
    - If dev helpers are available: run a full YAML regeneration (sbt clean compile + run per class)
      and then deploy ALL generated YAMLs under workflows/yaml_gens.
    - If dev helpers are unavailable: fall back to touching Scala workflow sources to trigger any
      running watchers to regenerate/deploy.
    - Logs a clear summary of actions taken.
    """
    orig = Path(get_orig_cwd())

    # Preferred path: generate and deploy directly
    if discover_paths and generate_yamls and deploy_workflow:
        paths = discover_paths()
        log_unique("🔧 Starting full regenerate + deploy of all workflows …")
        _ = generate_yamls(paths)  # clears yaml_gens and regenerates all classes

        yaml_files = _list_yaml_files(Path(paths.yaml_gens_dir))
        if not yaml_files:
            log_unique(
                f"⚠️ No YAMLs found under {paths.yaml_gens_dir} after regeneration. "
                "Falling back to touch-only behavior."
            )
        else:
            location = os.getenv("AWFL_GCLOUD_LOCATION", "us-central1")
            project = os.getenv("PROJECT", "topaigents")

            total = len(yaml_files)
            ok = 0
            for yf in yaml_files:
                if deploy_workflow(str(yf), location, project):  # type: ignore[arg-type]
                    ok += 1
            log_unique(f"📦 Deploy summary: {ok}/{total} workflows deployed from yaml_gens.")
            # If we successfully deployed any, we're done
            if ok > 0:
                return True

    # Fallback path: try to touch Scala sources to let external watcher handle it
    repo_root = _find_git_root(orig)
    scala_files = _gather_scala_sources(repo_root)
    if not scala_files:
        log_unique(
            "ℹ️ No Scala workflow sources found under expected path: "
            f"{repo_root}/workflows/src/main/scala/workflows"
        )
        return True

    touched = 0
    for f in scala_files:
        try:
            f.touch()
            touched += 1
        except Exception as e:
            log_unique(f"⚠️ Failed to touch {f}: {e}")

    if touched:
        rel_base = repo_root
        log_unique(f"🚀 Touched {touched} Scala workflow source(s) under {rel_base}")
        log_unique("If a watcher is running, it should regenerate and deploy them shortly.")
    else:
        log_unique("ℹ️ No files were touched.")

    return True
