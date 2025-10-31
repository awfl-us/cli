from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from awfl.utils import log_unique


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            ans = input(f"{question} {suffix} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return default
        if ans == "":
            return default
        if ans in ("y", "yes"):  # type: ignore[return-value]
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer y or n.")


def _prompt_value(question: str, default: Optional[str] = None) -> str:
    prompt = f"{question} [{default}] " if default is not None else f"{question} "
    try:
        ans = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return default or ""
    ans = ans.strip()
    return ans if ans else (default or "")


def _ensure_env(repo_root: str) -> bool:
    """Mimic dev.sh check_env: ensure .env exists, create from .env.example if present and stop."""
    env_file = Path(repo_root) / ".env"
    if env_file.exists():
        return True
    example = Path(repo_root) / ".env.example"
    if example.exists():
        try:
            log_unique("Creating .env file from template…")
            shutil.copyfile(example, env_file)
            log_unique("Please update the .env file with your API keys")
        except Exception as e:
            log_unique(f"⚠️ Failed to create .env from template: {e}")
        return False
    # If no example, allow continuing but warn
    log_unique("ℹ️ No .env found and no .env.example to copy. Continuing without it.")
    return True


__all__ = [
    "_prompt_yes_no",
    "_prompt_value",
    "_ensure_env",
]
