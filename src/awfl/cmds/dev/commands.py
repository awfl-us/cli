from __future__ import annotations

from typing import List

from awfl.utils import log_unique
from awfl import state

# Subcommand implementations are split into focused modules under subcommands/
from .subcommands import (
    start_dev,
    stop_dev,
    logs_cmd,
    generate_yamls_cmd,
    deploy_workflow_cmd,
    status_cmd,
)


HELP_TEXT = (
    "Dev commands:\n"
    "  dev status\n"
    "  dev on | dev off   (toggle persisted dev mode; affects workflow name suffix only)\n"
    "  dev start [--no-ngrok] [--no-compose] [--no-watch] [--port N] "
    "[--auto-deploy=on|off] [--compose-file PATH] [--workflows-dir PATH] "
    "[--location REGION] [--project ID] [--reconfigure|-r] [-y|--yes] [--no-prompt]\n"
    "  dev watch  (alias for: dev start --no-ngrok --no-compose)\n"
    "  dev stop [--no-ngrok] [--no-compose]\n"
    "  dev logs [--follow]\n"
    "  dev generate-yamls\n"
    "  dev deploy-workflow workflows/yaml_gens/<file>.yaml\n"
)


def handle_dev_command(args: List[str]) -> bool:
    if not args or args[0] in {"help", "-h", "--help"}:
        log_unique(HELP_TEXT)
        return True

    sub = args[0]
    rest = args[1:]

    if sub == "on":
        state.set_dev_mode(True)
        suf = state.get_workflow_env_suffix()
        log_unique(f"✅ Dev mode ON (suffix {suf if suf is not None else ''}).")
        return True
    if sub == "off":
        state.set_dev_mode(False)
        suf = state.get_workflow_env_suffix()
        log_unique(f"✅ Dev mode OFF (suffix {suf if suf is not None else ''}).")
        return True

    if sub == "start":
        return start_dev(rest)
    if sub == "watch":
        # Enforce no ngrok and no compose by appending flags so they take precedence
        return start_dev(rest + ["--no-ngrok", "--no-compose"])
    if sub == "stop":
        return stop_dev(rest)
    if sub == "logs":
        return logs_cmd(rest)
    if sub in ("generate-yamls", "gen-yamls", "gen"):
        return generate_yamls_cmd(rest)
    if sub in ("deploy-workflow", "deploy"):
        return deploy_workflow_cmd(rest)
    if sub == "status":
        return status_cmd(rest)

    log_unique("Unknown dev subcommand. Try: dev help")
    return True


__all__ = [
    "handle_dev_command",
]
