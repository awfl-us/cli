from __future__ import annotations

import uuid
import shlex
from typing import Callable, Dict, List, Any

from awfl.utils import log_unique
from awfl.utils import trigger_workflow

from .workflows import ls_workflows_interactive
from .exec_ctl import stop_or_cancel_active
from .auth_cmds import handle_login, print_whoami, handle_logout
from .config_cmds import set_exec_mode, set_api_origin, set_skip_auth, set_token_override, print_status
from .model_cmds import get_or_set_model
from .deploy_cmds import deploy_workflows, deploy_awfl_workflows
from .dev import handle_dev_command
from .files_cmds import upload_files_cmd


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
        "  deploy awfl workflows [--force]\n"
        "  upload files [--delete]\n"
        "  dev <subcommand>  (dev help for details)\n"
    )
    return True


def _coerce_primitive(val: str) -> Any:
    s = val.strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    # Try int, then float
    try:
        # Avoid interpreting hex or octal prefixed strings; int() handles plain decimals by default
        if s and (s.isdigit() or (s[0] in "+-" and s[1:].isdigit())):
            return int(s)
    except Exception:
        pass
    try:
        # Allow simple floats, including scientific notation
        return float(s)
    except Exception:
        pass
    return s


def _handle_call(args: List[str]) -> bool:
    if not args:
        log_unique("Usage: call <workflow> [args...]  (positional -> arg1..n; key=value supported)")
        return True
    wf = args[0]
    data_args = args[1:]

    # Use a one-off ephemeral tag for this call without changing SSE session filtering
    sid = str(uuid.uuid4())

    positional: List[Any] = []
    named: Dict[str, Any] = {}

    for tok in data_args:
        if "=" in tok:
            key, value = tok.split("=", 1)
            k = key.strip()
            if not k:
                # Treat malformed =value tokens as positional
                positional.append(_coerce_primitive(value))
                continue
            named[k] = _coerce_primitive(value)
        else:
            positional.append(_coerce_primitive(tok))

    payload: Dict[str, Any] = {}
    if data_args:
        payload["text"] = " ".join(data_args)

    # Map positional args -> arg1, arg2, ...
    for i, v in enumerate(positional, start=1):
        payload[f"arg{i}"] = v

    # Include named key=value pairs directly
    payload.update(named)

    payload["sessionId"] = sid
    payload["background"] = True

    # Concise parse summary
    pos_cnt = len(positional)
    named_keys = ", ".join(named.keys()) if named else "none"
    pos_suffix = f" (arg1..arg{pos_cnt})" if pos_cnt else ""
    log_unique(
        f"➡ call {wf}: background execution; tagged sessionId={sid} (SSE session unchanged); parsed {pos_cnt} positional{pos_suffix} and {len(named)} named keys [{named_keys}]"
    )

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
    if cmd.startswith("deploy awfl workflows"):
        parts = shlex.split(line)
        force = "--force" in parts[3:]
        return deploy_awfl_workflows(force=force)
    if cmd.startswith("upload files"):
        parts = shlex.split(line)
        # everything after the first two tokens are flags/args for the upload command
        args = parts[2:] if len(parts) >= 2 else []
        return upload_files_cmd(args)
    if cmd.startswith("dev ") or cmd == "dev":
        parts = shlex.split(line)
        return handle_dev_command(parts[1:])

    # Prefixed commands
    if cmd.startswith("call "):
        parts = shlex.split(line)
        return _handle_call(parts[1:])

    # Fall-through: treat as free text -> call current workflow
    return False
