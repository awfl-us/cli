from pathlib import Path
from typing import List, Dict, Any, Tuple, Set
import os

from awfl.utils import log_unique
from .dev.core import load_dev_config

# Lightweight path discovery is always available
from .dev.paths import discover_paths
# Deploy/generation helpers may be heavy; import lazily with fallback
try:
    from .dev.yaml_ops import generate_yamls, generate_for_classes, deploy_workflow  # type: ignore
except Exception:  # pragma: no cover - helpers unavailable
    generate_yamls = None  # type: ignore
    generate_for_classes = None  # type: ignore
    deploy_workflow = None  # type: ignore

# Unified project/location resolver
from .dev.dev_config import resolve_location_project


def _list_yaml_files(root: Path) -> List[Path]:
    files: List[Path] = []
    if root.exists():
        files = [f for f in sorted(root.rglob("*.yaml")) if f.is_file()]
    return files


def _scala_src_root(paths) -> Path:
    return Path(paths.workflows_dir) / "src" / "main" / "scala"


def deploy_workflows() -> bool:
    """Rebuild and deploy all workflows in one command.

    Behavior:
    - Requires dev helpers (sbt/gcloud). Runs a full YAML regeneration, ensures core AWFL
      utility workflows are generated, then deploys all YAMLs under workflows/yaml_gens.
    - No touch-based fallback.
    - Logs a clear summary of actions taken.
    """
    # Discover repo and workflow paths once
    cfg: Dict[str, Any] = load_dev_config() or {}
    paths = discover_paths(cfg)

    # Require helpers
    if not (generate_yamls and deploy_workflow):
        log_unique("❌ Dev helpers unavailable (sbt/gcloud). Cannot deploy workflows.")
        return False

    log_unique("🔧 Starting full regenerate + deploy of all workflows …")

    # Clears yaml_gens and regenerates all classes
    _ = generate_yamls(paths)  # type: ignore[arg-type]

    # Also ensure core AWFL utility workflows are generated explicitly
    try:
        deploy_awfl_workflows(generate_only=True)
    except Exception as e:
        # Do not fail the whole deploy if this helper call encounters a non-fatal issue
        log_unique(f"ℹ️ Continuing after attempt to generate core AWFL workflows: {e}")

    yaml_files = _list_yaml_files(Path(paths.yaml_gens_dir))
    if not yaml_files:
        log_unique(
            f"⚠️ No YAMLs found under {paths.yaml_gens_dir} after regeneration."
        )
        return False

    location, project = resolve_location_project()

    total = len(yaml_files)
    ok = 0
    for yf in yaml_files:
        if deploy_workflow(str(yf), location, project):  # type: ignore[arg-type]
            ok += 1
    log_unique(
        f"📦 Deploy summary: {ok}/{total} workflows deployed from yaml_gens (project={project}, location={location})."
    )

    return ok > 0


def _candidate_yaml_basenames_for_class(cls: str) -> List[str]:
    """Return possible YAML basenames (without directory) for a given class.

    Primary convention is fully-qualified class with dots: e.g.,
    us.awfl.workflows.helpers.ToolDefs.yaml
    Some generators may omit the first segment (e.g., `us.`), so also try
    awfl.workflows.helpers.ToolDefs.yaml
    """
    basenames = [f"{cls}.yaml"]
    if "." in cls:
        basenames.append(f"{cls.split('.', 1)[1]}.yaml")
    return basenames


def _find_yaml_for_class(yaml_gens_dir: Path, cls: str, changed_paths: List[str]) -> Path | None:
    """Find the best YAML file path for the given class.

    Preference order:
    1) Exact basename match under yaml_gens.
    2) Basename match with first segment removed.
    3) Among recently changed files, the first whose basename without extension endswith the class name.
    4) Fallback to any file in yaml_gens whose basename contains the final class segment.
    """
    # 1 & 2: direct basenames
    for base in _candidate_yaml_basenames_for_class(cls):
        p = yaml_gens_dir / base
        if p.exists():
            return p

    # 3: use changed paths signal
    for p in changed_paths:
        try:
            name = Path(p).name
        except Exception:
            continue
        stem = name.rsplit(".", 1)[0]
        if stem.endswith(cls) or ("." in cls and stem.endswith(cls.split(".", 1)[1])):
            return Path(p)

    # 4: fuzzy search in directory
    final_seg = cls.split(".")[-1]
    for f in sorted(yaml_gens_dir.glob("*.yaml")):
        if final_seg in f.stem:
            return f
    return None


def _group_and_name_from_class(cls: str) -> Tuple[str, str] | None:
    """Extract the package 'group' immediately under 'workflows' and the class 'Name'.

    Example: us.awfl.workflows.codebase.ProjectManager -> (codebase, ProjectManager)
    """
    parts = cls.split(".")
    try:
        idx = parts.index("workflows")
        group = parts[idx + 1]
        name = parts[idx + 2]
        return group, name
    except Exception:
        return None


def _find_yamls_for_class(yaml_gens_dir: Path, cls: str) -> List[Path]:
    """Find all YAMLs generated for a given class using package-aware matching.

    We match files like '{group}-{Name}*.yaml' to capture multi-file outputs such as
    codebase-ProjectManager.yaml and codebase-ProjectManager-prompts.yaml.
    This avoids cross-package collisions (e.g., helpers-Tasks vs tools-Tasks).
    """
    out: List[Path] = []
    gn = _group_and_name_from_class(cls)
    if gn is None:
        return out
    group, name = gn
    pattern = f"{group}-{name}*.yaml"
    out = sorted([p for p in yaml_gens_dir.glob(pattern) if p.is_file()])
    return out


def deploy_awfl_workflows(generate_only: bool = False, force: bool = False) -> bool:
    """Generate (and optionally deploy) a curated set of AWFL utility workflows by class name.

    Default behavior now deploys only the files returned in the generator's 'changed' list.
    No sibling expansion or deletion handling is performed.

    When generate_only is True, only generation is performed so YAMLs are included
    in subsequent bulk deploys.
    """
    cfg: Dict[str, Any] = load_dev_config() or {}
    paths = discover_paths(cfg)
    yaml_gens_dir = Path(paths.yaml_gens_dir)

    core_classes: List[str] = [
        "us.awfl.workflows.codebase.ProjectManager",
        "us.awfl.workflows.helpers.ToolDefs",
        "us.awfl.workflows.helpers.ToolDispatcher",
        "us.awfl.workflows.helpers.Tasks",
        "us.awfl.workflows.tools.CliTools",
        "us.awfl.workflows.tools.Tasks",
        "us.awfl.workflows.Summaries",
        "us.awfl.workflows.assistant.ExtractTopics",
        "us.awfl.workflows.context.ContextCollapser",
        "us.awfl.workflows.helpers.Chain",
        "us.awfl.workflows.helpers.links.SaveReflection"
    ]

    if not (generate_for_classes):
        log_unique("❌ Dev helpers unavailable (sbt). Cannot generate core AWFL workflows.")
        return False

    log_unique("🔧 Generating YAMLs for core AWFL workflows …")
    changed = generate_for_classes(paths, core_classes)  # type: ignore[arg-type]

    if generate_only:
        # Generation only; rely on caller to deploy from yaml_gens
        if not changed:
            log_unique("ℹ️ No changes detected while generating core AWFL workflows.")
        return True

    if not deploy_workflow:
        log_unique("❌ Dev helpers unavailable (gcloud). Cannot deploy core AWFL workflows.")
        return False

    # Changed-only deploy path: resolve only the files reported by the generator
    changed_list: List[str] = changed or []

    # Resolve to actual file paths under yaml_gens (or absolute if provided)
    resolved_paths: List[Path] = []
    for p in changed_list:
        try:
            pp = Path(p)
            if not pp.is_absolute():
                cand = yaml_gens_dir / p
                if cand.exists():
                    pp = cand
            # If still not found, try matching by basename inside yaml_gens
            if not pp.exists():
                cand2 = yaml_gens_dir / Path(p).name
                if cand2.exists():
                    pp = cand2
            if pp.exists():
                resolved_paths.append(pp)
            else:
                log_unique(f"⚠️ Skipping missing YAML from changed list: {p}")
        except Exception as e:
            log_unique(f"⚠️ Skipping invalid path from changed list: {p} ({e})")

    if not resolved_paths:
        if not force:
            log_unique("ℹ️ No changes detected; skipping deploy of AWFL workflows.")
            return True
        # --force fallback: preserve previous behavior by selecting YAMLs per class
        log_unique("⚠️ No changed files reported; --force set, falling back to class-based selection.")
        to_deploy_paths: List[Path] = []
        for cls in core_classes:
            matches = _find_yamls_for_class(yaml_gens_dir, cls)
            if not matches:
                # Fall back to legacy single-file discovery to be robust if naming changes
                legacy = _find_yaml_for_class(yaml_gens_dir, cls, changed_paths=[])
                if legacy and legacy.exists():
                    matches = [legacy]
            if matches:
                to_deploy_paths.extend(matches)
            else:
                log_unique(f"⚠️ No YAMLs found for {cls} under {yaml_gens_dir}")
        resolved_paths = to_deploy_paths

    # De-duplicate while preserving order
    seen: Set[str] = set()
    unique_paths: List[Path] = []
    for p in resolved_paths:
        sp = str(p)
        if sp not in seen:
            seen.add(sp)
            unique_paths.append(p)

    if not unique_paths:
        log_unique("⚠️ Nothing to deploy after resolution.")
        return True

    location, project = resolve_location_project()

    ok = 0
    for yf in unique_paths:
        # Log which workflow name is being deployed for clarity
        log_unique(f"🚀 Deploying {yf.stem} …")
        if deploy_workflow(str(yf), location, project):  # type: ignore[arg-type]
            ok += 1

    log_unique(
        f"📦 Deploy summary (core AWFL, changed-only): {ok}/{len(unique_paths)} deployed (project={project}, location={location})."
    )
    return ok > 0
