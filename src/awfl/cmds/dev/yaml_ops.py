from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Iterable, Tuple
import tempfile

from awfl.utils import log_unique
from .paths import DevPaths
from .dev_state import set_state


def _snapshot_yaml_mtimes(yaml_dir: str) -> Dict[str, float]:
    mtimes: Dict[str, float] = {}
    p = Path(yaml_dir)
    if not p.exists():
        return mtimes
    for fp in p.glob("**/*.yaml"):
        try:
            mtimes[str(fp)] = fp.stat().st_mtime
        except FileNotFoundError:
            pass
    return mtimes


def _ensure_dirs(paths: DevPaths, *, clear_yaml_gens: bool) -> None:
    wf = Path(paths.workflows_dir)
    yg = Path(paths.yaml_gens_dir)
    if not wf.exists():
        log_unique(f"❌ Workflows dir not found: {wf}")
        return
    if clear_yaml_gens and yg.exists():
        try:
            shutil.rmtree(yg)
            log_unique(f"🧹 Cleared directory: {yg}")
        except Exception as e:
            log_unique(f"⚠️ Failed to clear {yg}: {e}")
    yg.mkdir(parents=True, exist_ok=True)


def _scala_src_root(paths: DevPaths) -> Path:
    # workflows/src/main/scala
    return Path(paths.workflows_dir) / "src" / "main" / "scala"


def _class_path_from_scala_file(paths: DevPaths, file_path: str) -> str | None:
    try:
        root = _scala_src_root(paths)
        rel = Path(file_path).resolve().relative_to(root.resolve())
        # strip .scala, replace / with .
        base = str(rel.with_suffix(""))
        return base.replace(os.sep, ".")
    except Exception:
        return None


def _discover_scala_classes(paths: DevPaths) -> List[str]:
    root = _scala_src_root(paths)
    if not root.exists():
        return []
    classes: List[str] = []
    for f in root.rglob("*.scala"):
        cp = _class_path_from_scala_file(paths, str(f))
        if cp:
            classes.append(cp)
    return classes


def _run_sbt_in_workflows(paths: DevPaths, args: List[str]) -> Tuple[int, str, str]:
    # Use bash -lc to honor shell quoting like sbt "run foo bar"
    cmd = f"cd {shlex.quote(paths.workflows_dir)} && {' '.join(shlex.quote(a) for a in args)}"
    res = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def generate_for_classes(paths: DevPaths, classes: Iterable[str]) -> List[str]:
    """Generate YAMLs only for the given Scala classes (dot-paths).

    Mirrors dev.sh watch_workflows behavior: runs `sbt "run <class> yaml_gens"` for each.
    Does NOT clear yaml_gens; intended for incremental regeneration.
    """
    if not shutil.which("sbt"):
        log_unique("⚠️ sbt is not installed or not in PATH. Skipping generation.")
        return []

    classes = list(dict.fromkeys(classes))  # dedupe, preserve order
    if not classes:
        log_unique("ℹ️ No classes to regenerate.")
        return []

    before = _snapshot_yaml_mtimes(paths.yaml_gens_dir)

    log_unique(f"🔧 Incremental YAML generation for {len(classes)} class(es)…")
    log_unique(f"  • workflows_dir: {paths.workflows_dir}")
    log_unique(f"  • yaml_gens_dir: {paths.yaml_gens_dir}")

    for cp in classes:
        sbt_arg = f"run {cp} yaml_gens"
        log_unique(f"➡️ sbt \"{sbt_arg}\"")
        code, out, err = _run_sbt_in_workflows(paths, ["sbt", sbt_arg])
        if code != 0:
            tail = "\n".join((err or out or "").splitlines()[-40:])
            log_unique(f"❌ sbt run failed for {cp} (code {code}). Tail:\n{tail}")
        else:
            # Optional: surface a brief success indicator per class
            log_unique(f"✅ Generated YAML(s) for {cp}")

    after = _snapshot_yaml_mtimes(paths.yaml_gens_dir)
    changed = [p for p, mt in after.items() if before.get(p) != mt]
    set_state(last_yaml_snapshot=after, last_changed=changed)
    if changed:
        log_unique("📄 New/updated YAMLs:\n- " + "\n- ".join(_short_display(paths, c) for c in changed))
    else:
        log_unique("ℹ️ No YAML changes detected.")
    return changed


def generate_yamls(paths: DevPaths) -> List[str]:
    """Full regeneration of YAMLs for ALL workflow classes.

    Closer to scripts/regenerate_and_deploy.sh: clears yaml_gens and runs
    `sbt "run <class> yaml_gens"` for each discovered Scala class under workflows.
    As a fast-fail pre-step, we still try `sbt clean compile` to catch compile errors early.
    """
    if not shutil.which("sbt"):
        log_unique("⚠️ sbt is not installed or not in PATH. Skipping build.")
        return []

    _ensure_dirs(paths, clear_yaml_gens=True)

    # Log environment info to diagnose failures
    log_unique("🔧 Running full YAML regeneration …")
    log_unique(f"  • workflows_dir: {paths.workflows_dir}")
    log_unique(f"  • scala_src_dir: {paths.scala_src_dir}")
    log_unique(f"  • yaml_gens_dir: {paths.yaml_gens_dir}")
    log_unique(f"  • sbt: {shutil.which('sbt')}")

    # Pre-step: clean compile to avoid repeating compilation per class
    log_unique("🧱 sbt clean compile …")
    code, out, err = _run_sbt_in_workflows(paths, ["sbt", "clean", "compile"])
    if code != 0:
        tail = "\n".join((err or out or "").splitlines()[-80:])
        log_unique(f"❌ sbt clean compile failed (code {code}). Tail:\n{tail}")
        return []

    classes = _discover_scala_classes(paths)
    if not classes:
        log_unique("⚠️ No Scala workflow classes found.")
        return []

    log_unique(f"🔎 Discovered {len(classes)} workflow class(es).")

    before = _snapshot_yaml_mtimes(paths.yaml_gens_dir)

    for cp in classes:
        sbt_arg = f"run {cp} yaml_gens"
        log_unique(f"➡️ sbt \"{sbt_arg}\"")
        code, out, err = _run_sbt_in_workflows(paths, ["sbt", sbt_arg])
        if code != 0:
            tail = "\n".join((err or out or "").splitlines()[-60:])
            log_unique(f"❌ sbt run failed for {cp} (code {code}). Tail:\n{tail}")
        else:
            log_unique(f"✅ Generated YAML(s) for {cp}")

    after = _snapshot_yaml_mtimes(paths.yaml_gens_dir)
    changed = [p for p, mt in after.items() if before.get(p) != mt]
    set_state(last_yaml_snapshot=after, last_changed=changed)
    if changed:
        log_unique("✅ YAMLs updated:\n- " + "\n- ".join(_short_display(paths, c) for c in changed))
    else:
        log_unique("ℹ️ No YAML changes detected.")
    return changed


def _short_display(paths: DevPaths, p: str) -> str:
    try:
        return str(Path(p).relative_to(paths.yaml_gens_dir))
    except Exception:
        return p


def derive_workflow_name(yaml_path: str) -> str:
    # Match legacy dev.sh behavior:
    # 1) take basename without extension
    # 2) drop the first segment before the first dot, if present
    # 3) replace remaining dots with dashes
    name = Path(yaml_path).name
    base = name.rsplit(".", 1)[0]
    if "." in base:
        base = base.split(".", 1)[1]
    return base.replace(".", "-")


def _env_suffix() -> str:
    return os.getenv("WORKFLOW_ENV", "Dev")


def _prepare_deploy_source(yaml_path: str) -> str:
    """Prepare a deployable YAML source path.

    To mirror dev.sh, replace ${WORKFLOW_ENV} with the current suffix, but do not touch
    other placeholders (e.g., BASE_URL is a workflow parameter now).
    Writes a temporary file and returns its path.
    """
    try:
        content = Path(yaml_path).read_text(encoding="utf-8")
    except Exception as e:
        log_unique(f"⚠️ Could not read {yaml_path}: {e}")
        return yaml_path

    replaced = content.replace("${WORKFLOW_ENV}", _env_suffix())
    if replaced == content:
        # No substitution occurred; return the original path
        return yaml_path

    try:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        with tmp as f:
            f.write(replaced)
        return tmp.name
    except Exception as e:
        log_unique(f"⚠️ Failed to write temp deploy YAML: {e}")
        return yaml_path


def deploy_workflow(yaml_path: str, location: str, project: str) -> bool:
    if not shutil.which("gcloud"):
        log_unique("⚠️ gcloud not found in PATH; cannot deploy.")
        return False
    name = derive_workflow_name(yaml_path) + _env_suffix()
    source_path = _prepare_deploy_source(yaml_path)

    args = [
        "gcloud",
        "workflows",
        "deploy",
        name,
        "--source",
        source_path,
        "--location",
        location,
        "--project",
        project,
        "--execution-history-level",
        "execution-history-detailed",
    ]
    log_unique(f"🚀 Deploying {name} …")
    res = subprocess.run(args, text=True)
    ok = res.returncode == 0
    if ok:
        log_unique(f"✅ Deployed {name} to {project}/{location}")
    else:
        log_unique(f"❌ Deploy failed (code {res.returncode}).")
    # Cleanup temp file if we created one
    try:
        if source_path != yaml_path:
            Path(source_path).unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    return ok


__all__ = [
    "_snapshot_yaml_mtimes",
    "generate_yamls",
    "generate_for_classes",
    "_short_display",
    "derive_workflow_name",
    "_env_suffix",
    "deploy_workflow",
]