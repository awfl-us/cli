from __future__ import annotations

import os
import time
import atexit
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional

from awfl.utils import log_unique

from ..core import (
    DevPaths,
    discover_paths,
    start_ngrok,
    stop_ngrok,
    compose_up,
    compose_status,
    _snapshot_yaml_mtimes,
    _short_display,
    _env_suffix,
    watch_workflows,
    get_state,
    set_state,
    load_dev_config,
    save_dev_config,
)
from ..prompt_utils import _prompt_yes_no, _prompt_value, _ensure_env
from ..paths import _detect_scala_watch_dir

# Late import inside functions to avoid circulars when stop imports core
from .stop import stop_dev  # type: ignore


def _dev_shutdown_once(reason: str = "") -> None:
    state = get_state()
    if state.get("dev_shutdown_done"):
        return
    set_state(dev_shutdown_done=True)
    if reason:
        log_unique(f"Shutting down dev services ({reason}) …")
    try:
        # Best-effort stop; ignore flags so we fully mirror dev.sh
        stop_dev([])
    except Exception:
        pass


def _register_shutdown_hooks() -> None:
    state = get_state()
    if state.get("dev_shutdown_hooks_registered"):
        return

    def _sig_handler(signum, frame):
        name = "SIGINT" if signum == signal.SIGINT else ("SIGTERM" if signum == signal.SIGTERM else str(signum))
        _dev_shutdown_once(reason=name)
        # Re-raise default behavior after cleanup for SIGINT (KeyboardInterrupt)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        else:
            os._exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        # Some environments (threads, certain shells) disallow signal setup
        pass

    atexit.register(lambda: _dev_shutdown_once(reason="atexit"))
    set_state(dev_shutdown_hooks_registered=True)


def start_dev(args: List[str]) -> bool:
    # Defaults (lowest precedence)
    port = int(os.getenv("AWFL_NGROK_PORT", "8081") or 8081)
    auto_deploy = (os.getenv("AUTO_DEPLOY", "on").lower() != "off")
    use_ngrok = True
    use_compose = True
    use_watch = True
    compose_file_override: Optional[str] = None
    workflows_dir_override: Optional[str] = None
    location = os.getenv("AWFL_GCLOUD_LOCATION", "us-central1")
    project = os.getenv("PROJECT")

    # Load persisted config early so CLI flags can override it (flags > config > env/defaults)
    cfg: Dict[str, Any] = load_dev_config() or {}
    port = int(cfg.get("ngrok_port", port))
    auto_deploy = bool(cfg.get("auto_deploy", auto_deploy))
    use_ngrok = bool(cfg.get("use_ngrok", use_ngrok))
    use_compose = bool(cfg.get("use_compose", use_compose))
    use_watch = bool(cfg.get("use_watch", use_watch))
    location = cfg.get("location", location)
    project = cfg.get("project", project)

    # Flags
    reconfigure = False
    no_prompt = False
    yes_all = False

    it = iter(args)
    for tok in it:
        if tok == "--no-ngrok":
            use_ngrok = False
        elif tok == "--no-compose":
            use_compose = False
        elif tok == "--no-watch":
            use_watch = False
        elif tok == "--port":
            try:
                port = int(next(it))
            except Exception:
                log_unique("⚠️ --port requires an integer.")
        elif tok.startswith("--auto-deploy"):
            val = tok.split("=", 1)[1] if "=" in tok else (next(it, "on"))
            auto_deploy = val.lower() == "on"
        elif tok == "--compose-file":
            compose_file_override = next(it, None)
        elif tok == "--workflows-dir":
            workflows_dir_override = next(it, None)
        elif tok == "--location":
            location = next(it, location)
        elif tok == "--project":
            project = next(it, project)
        elif tok in ("--reconfigure", "-r"):
            reconfigure = True
        elif tok in ("--no-prompt",):
            no_prompt = True
        elif tok in ("-y", "--yes"):
            yes_all = True
        else:
            log_unique(f"ℹ️ Ignoring unknown flag: {tok}")

    # Discover paths and apply overrides (from either flags or persisted cfg)
    paths = discover_paths(cfg)
    if workflows_dir_override:
        paths.workflows_dir = workflows_dir_override
        paths.yaml_gens_dir = str(Path(paths.workflows_dir) / "yaml_gens")
        # Recompute scala watch dir using smarter detection
        paths.scala_src_dir = str(_detect_scala_watch_dir(Path(paths.workflows_dir)))
    if compose_file_override:
        paths.compose_file = compose_file_override

    # Ensure .env like dev.sh before starting anything
    if not _ensure_env(paths.repo_root):
        return False

    # First-run or reconfigure interactive confirmation
    needs_prompt = (not cfg.get("confirmed")) or reconfigure
    if not no_prompt and (needs_prompt or yes_all):
        log_unique("Configuring dev environment …")
        use_ngrok = yes_all or _prompt_yes_no("Start/attach ngrok tunnel?", default=use_ngrok)
        if use_ngrok:
            port = int(_prompt_value("Local port to expose via ngrok:", str(port)) or port)
        use_compose = yes_all or _prompt_yes_no("Manage docker compose?", default=use_compose)
        if use_compose:
            detected = paths.compose_file or "none"
            new_cf = _prompt_value("Compose file path (enter to keep)", detected)
            paths.compose_file = new_cf if new_cf and new_cf.lower() != "none" else paths.compose_file
        use_watch = yes_all or _prompt_yes_no("Start Scala watcher?", default=use_watch)
        workflows_dir_override = _prompt_value("Workflows dir (enter to keep)", paths.workflows_dir) or paths.workflows_dir
        paths.workflows_dir = workflows_dir_override
        paths.yaml_gens_dir = str(Path(paths.workflows_dir) / "yaml_gens")
        # Recompute scala watch dir using smarter detection
        paths.scala_src_dir = str(_detect_scala_watch_dir(Path(paths.workflows_dir)))
        location = _prompt_value("GCloud location", location or "us-central1") or location
        project = _prompt_value("GCloud project", project or "") or project
        auto_deploy = yes_all or _prompt_yes_no("Auto-deploy changed YAMLs?", default=auto_deploy)

        if _prompt_yes_no("Save these settings for future dev sessions?", default=True):
            cfg = {
                "confirmed": True,
                "ngrok_port": port,
                "auto_deploy": auto_deploy,
                "use_ngrok": use_ngrok,
                "use_compose": use_compose,
                "use_watch": use_watch,
                "compose_file": paths.compose_file,
                "workflows_dir": paths.workflows_dir,
                "location": location,
                "project": project,
            }
            save_dev_config(cfg)

    # Start services according to config
    if use_ngrok:
        proc = start_ngrok(port)
        # Track both the process and the port for reliable teardown
        set_state(ngrok_proc=proc, ngrok_port=port)
        # Wait briefly for ngrok to expose a tunnel and export BASE_URL like dev.sh
        from ..core import _get_ngrok_existing_url
        tunnel_url: Optional[str] = None
        for _ in range(20):  # ~5s
            tunnel_url = _get_ngrok_existing_url()
            if tunnel_url:
                break
            time.sleep(0.25)
        if tunnel_url:
            os.environ["WORKFLOWS_BASE_URL"] = f"{tunnel_url}/jobs"
            log_unique(f"Ngrok tunnel established at: {tunnel_url}")
        else:
            log_unique("⚠️ Failed to retrieve ngrok URL!")
            # Stop ngrok we just started and abort to mirror dev.sh failure
            try:
                if proc:
                    stop_ngrok(proc, port=port)
            finally:
                set_state(ngrok_proc=None)
            return False
    else:
        # Still track the desired port for potential teardown if an external ngrok is running
        set_state(ngrok_port=port)

    if use_compose and paths.compose_file and Path(paths.compose_file).exists():
        if compose_up(paths.compose_file):
            set_state(compose_started_here=True)
    elif use_compose:
        log_unique("ℹ️ No docker compose file discovered; skipping.")

    set_state(last_yaml_snapshot=_snapshot_yaml_mtimes(paths.yaml_gens_dir))

    if use_watch:
        task = watch_workflows(paths, auto_deploy=auto_deploy)
        set_state(watcher_task=task)

    # Register clean shutdown hooks so Ctrl-C or process exit mirrors dev.sh teardown
    _register_shutdown_hooks()

    from ..core import _get_ngrok_existing_url
    tunnel = _get_ngrok_existing_url()
    comp = compose_status(paths.compose_file)
    # Re-fetch fresh state to reflect watcher_task set above
    cur_state = get_state()
    watch = "running" if cur_state.get("watcher_task") and not cur_state.get("watcher_task").done() else "stopped"

    log_unique(
        "Dev started with:\n"
        f"- repo: {paths.repo_root}\n"
        f"- workflows: {paths.workflows_dir}\n"
        f"- yaml_gens: {paths.yaml_gens_dir}\n"
        f"- ngrok: {tunnel or 'not started'} (port {port})\n"
        f"- compose: {paths.compose_file or 'none'} ({comp})\n"
        f"- watcher: {watch} (auto-deploy={'on' if auto_deploy else 'off'})\n"
        f"- deploy target: {project}/{location} (suffix {_env_suffix()})"
    )
    return True


__all__ = ["start_dev"]
