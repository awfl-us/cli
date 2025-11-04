from __future__ import annotations

import shlex
from typing import Callable, Dict, List, Tuple

from awfl.utils import log_unique
from awfl.state import set_active_workflow
from awfl.utils import trigger_workflow

from .workflows import ls_workflows_interactive
from .exec_ctl import stop_or_cancel_active
from .auth_cmds import handle_login, print_whoami, handle_logout
from .config_cmds import set_exec_mode, set_api_origin, set_skip_auth, set_token_override, print_status
from .model_cmds import get_or_set_model
from .deploy_cmds import deploy_workflows
from .dev import handle_dev_command


Handler = Callable[[List[str]], bool]


def _normalize(cmd: str) -> str:
    return " ".join(cmd.strip().split()).lower()


def _default_help() -> bool:
    log_unique(
        "Commands:\n"
        "  help | ?\n"
        "  status\n"
        "  login | auth login\n"
        "  whoami | auth status\n"
        "  logout | auth logout\n"
        # "  use api | exec api    (or: use gcloud | exec gcloud)\n"
        "  set api_origin <url>\n"
        "  auth skip on|off\n"
        "  auth set-token <idToken> | auth clear-token\n"
        "  model [name]\n"
        "  workflows | ls\n"
        "  call <workflow> [args...]\n"
        "  stop | cancel | abort\n"
        "  deploy workflows\n"
        "  dev <subcommand>  (dev help for details)\n"
    )
    return True


def _handle_call(args: List[str]) -> bool:
    if not args:
        log_unique("Usage: call <workflow> [args...]")
        return True
    wf = args[0]
    data_args = args[1:]
    set_active_workflow(wf)
    payload = {"text": " ".join(data_args)} if data_args else {}
    trigger_workflow(wf, payload)
    return True


def _handle_use_exec(args: List[str], mode: str) -> bool:
    return set_exec_mode(mode)


def _handle_set_api_origin(args: List[str]) -> bool:
    if not args:
        log_unique("Usage: set api_origin <url>")
        return True
    return set_api_origin(args[0])


def _handle_auth_skip(args: List[str]) -> bool:
    if not args:
        log_unique("Usage: auth skip on|off")
        return True
    on = args[0].lower() == "on"
    return set_skip_auth(on)


def _handle_auth_set_token(args: List[str]) -> bool:
    if not args:
        log_unique("Usage: auth set-token <idToken>")
        return True
    return set_token_override(args[0])


def _handle_auth_clear_token(args: List[str]) -> bool:
    return set_token_override(None)


def _handle_model(args: List[str]) -> bool:
    name = args[0] if args else None
    return get_or_set_model(name)


def handle_command(line: str) -> bool:
    cmd = _normalize(line)
    if not cmd:
        return True

    # Direct matches and aliases
    if cmd in ("help", "?", "h"):
        return _default_help()
    if cmd in ("status",):
        return print_status() or True
    if cmd in ("login", "auth login"):
        return handle_login()
    if cmd in ("whoami", "auth status"):
        print_whoami()
        return True
    if cmd in ("logout", "auth logout"):
        return handle_logout()
    if cmd in ("workflows", "ls"):
        ls_workflows_interactive()
        return True
    if cmd.startswith("use api") or cmd == "exec api" or cmd == "mode api":
        return _handle_use_exec([], "api")
    if cmd.startswith("use gcloud") or cmd == "exec gcloud" or cmd == "mode gcloud":
        return _handle_use_exec([], "gcloud")
    if cmd.startswith("auth skip "):
        parts = cmd.split()
        if len(parts) >= 3:
            return _handle_auth_skip([parts[2]])
        log_unique("Usage: auth skip on|off")
        return True
    if cmd.startswith("set api_origin "):
        parts = shlex.split(line)
        # find the argument after 'set' and 'api_origin'
        args = parts[2:3] if len(parts) >= 3 else []
        return _handle_set_api_origin(args)
    if cmd.startswith("auth set-token "):
        parts = shlex.split(line)
        token = parts[2] if len(parts) >= 3 else None
        return _handle_auth_set_token([token] if token else [])
    if cmd in ("auth clear-token",):
        return _handle_auth_clear_token([])
    if cmd.startswith("model"):
        parts = shlex.split(line)
        name = parts[1] if len(parts) >= 2 else None
        return _handle_model([name] if name else [])
    if cmd in ("stop", "cancel", "abort"):
        return stop_or_cancel_active()
    if cmd == "deploy workflows":
        return deploy_workflows()
    if cmd.startswith("dev ") or cmd == "dev":
        parts = shlex.split(line)
        return handle_dev_command(parts[1:])

    # Prefixed commands
    if cmd.startswith("call "):
        parts = shlex.split(line)
        return _handle_call(parts[1:])

    # Fall-through: treat as free text -> call current workflow
    return False
