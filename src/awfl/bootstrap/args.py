"""Startup argument/alias bootstrap helpers.

These helpers run very early in CLI startup to normalize simple aliases and
derive an optional startup command. They must be quiet, resilient, and avoid
raising exceptions on normal paths.
"""
from __future__ import annotations

import os
import sys
import shlex
from typing import Optional


def _argv_positionals() -> list[str]:
    try:
        return [a for a in sys.argv[1:] if a and isinstance(a, str) and not a.startswith("-")]
    except Exception:
        return []


def process_run_alias_from_argv() -> None:
    """Recognize 'run'|'headless'|'remote' aliases like the legacy entrypoint.

    Behavior (parity with main_old.py):
    - If the first positional token (non-flag) is one of {run, headless, remote}:
      - Set AWFL_NO_REPL=1 (force headless mode).
      - Capture the remainder of argv after that positional into AWFL_BOOTSTRAP_CMD
        using shlex.join (fallback to space-join on error).
      - Clear sys.argv to only the argv[0] program name so normal startup-cmd
        routing is skipped; bootstrap is executed inside the async runtime.
    - Otherwise: no-op.

    Notes
    - Does not set API_ORIGIN for 'remote' (matches legacy behavior).
    - Never raises; on any unexpected condition, it quietly no-ops.
    """
    try:
        pos = _argv_positionals()
        if not pos:
            return
        first = (pos[0] or "").strip().lower()
        if first not in ("run", "headless", "remote"):
            return

        # Force headless mode
        os.environ["AWFL_NO_REPL"] = "1"

        # Find index of the first positional in the real argv to preserve flags ordering if ever needed
        try:
            first_index = next(i for i, a in enumerate(sys.argv[1:], start=1) if a and isinstance(a, str) and not a.startswith('-'))
        except StopIteration:
            first_index = 1
        remainder = sys.argv[first_index + 1 :]
        if remainder:
            try:
                os.environ["AWFL_BOOTSTRAP_CMD"] = shlex.join(remainder)
            except Exception:
                os.environ["AWFL_BOOTSTRAP_CMD"] = " ".join(remainder)

        # Clear argv so downstream parsing doesn't re-handle these tokens
        sys.argv = [sys.argv[0]]
    except Exception:
        # Be maximally defensive; never let startup fail due to alias handling.
        return


def startup_command_from_argv() -> Optional[str]:
    """Return a startup command string derived from remaining argv, if any.

    Rules (parity with main_old.py):
    - No args -> None (enter interactive)
    - ['dev'] -> None (enter interactive in Dev mode)
    - Anything else -> join all args with shlex.join and run via handle_command

    Note: run/headless/remote aliases are pre-processed and removed from argv before this runs.
    """
    try:
        argv = sys.argv
        if not isinstance(argv, list):
            return None
        all_args = [str(x) for x in argv[1:] if x is not None]
        if not all_args:
            return None
        if len(all_args) == 1 and all_args[0].lower() == "dev":
            return None
        try:
            return shlex.join(all_args)
        except Exception:
            return " ".join(all_args)
    except Exception:
        return None


def is_long_running_startup(cmd: str) -> bool:
    """Detect long-running startup commands.

    Parity with main_old.py: only 'dev start' and 'dev watch' are treated as
    long-running so they can spawn asyncio tasks on the main loop and keep the
    CLI open. Everything else is short-lived.
    """
    try:
        try:
            parts = shlex.split(cmd)
        except Exception:
            parts = cmd.split()
        if not parts:
            return False
        if parts[0] == "dev" and len(parts) > 1 and parts[1] in ("start", "watch"):
            return True
        return False
    except Exception:
        return False
