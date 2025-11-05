from pathlib import Path
from typing import List, Dict, Any

from awfl.utils import log_unique
from .dev.core import load_dev_config

# Lightweight path discovery is always available
from .dev.paths import discover_paths
# Deploy/generation helpers may be heavy; import lazily with fallback
try:
    from .dev.yaml_ops import generate_yamls, deploy_workflow  # type: ignore
except Exception:  # pragma: no cover - fall back to touch mode only
    generate_yamls = None  # type: ignore
    deploy_workflow = None  # type: ignore

# Unified project/location resolver
from .dev.dev_config import resolve_location_project


def _list_yaml_files(root: Path) -> List[Path]:
    files: List[Path] = []
    if root.exists():
        files = [f for f in sorted(root.rglob("*.yaml")) if f.is_file()]
    return files


def _gather_scala_sources(scala_src_dir: Path) -> List[Path]:
    """Collect Scala source files under the detected Scala source directory."""
    if not scala_src_dir.exists():
        return []
    return [f for f in sorted(scala_src_dir.rglob("*.scala")) if f.is_file()]


def deploy_workflows() -> bool:
    """Rebuild and deploy all workflows in one command.

    Behavior:
    - If dev helpers are available: run a full YAML regeneration and then deploy all YAMLs
      under workflows/yaml_gens.
    - If dev helpers are unavailable: fall back to touching Scala workflow sources to trigger any
      running watchers to regenerate/deploy.
    - Logs a clear summary of actions taken.
    """
    # Discover repo and workflow paths once
    cfg: Dict[str, Any] = load_dev_config() or {}
    paths = discover_paths(cfg)

    # Preferred path: generate and deploy directly
    if generate_yamls and deploy_workflow:
        log_unique("🔧 Starting full regenerate + deploy of all workflows …")
        _ = generate_yamls(paths)  # clears yaml_gens and regenerates all classes

        yaml_files = _list_yaml_files(Path(paths.yaml_gens_dir))
        if not yaml_files:
            log_unique(
                f"⚠️ No YAMLs found under {paths.yaml_gens_dir} after regeneration. "
                "Falling back to touch-only behavior."
            )
        else:
            location, project = resolve_location_project()

            total = len(yaml_files)
            ok = 0
            for yf in yaml_files:
                if deploy_workflow(str(yf), location, project):  # type: ignore[arg-type]
                    ok += 1
            log_unique(
                f"📦 Deploy summary: {ok}/{total} workflows deployed from yaml_gens (project={project}, location={location})."
            )
            # If we successfully deployed any, we're done
            if ok > 0:
                return True

    # Fallback path: try to touch Scala sources to let external watcher handle it
    scala_src_dir = Path(paths.scala_src_dir)
    scala_files = _gather_scala_sources(scala_src_dir)
    if not scala_files:
        log_unique(
            "ℹ️ No Scala workflow sources found under expected path: "
            f"{scala_src_dir}"
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
        rel_base = Path(paths.repo_root)
        log_unique(f"🚀 Touched {touched} Scala workflow source(s) under {rel_base}")
        log_unique("If a watcher is running, it should regenerate and deploy them shortly.")
    else:
        log_unique("ℹ️ No files were touched.")

    return True
