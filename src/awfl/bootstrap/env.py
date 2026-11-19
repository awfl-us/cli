"""Environment-mode bootstrap helpers.

Implements light, safe parsing of a few environment-affecting flags from
sys.argv and initializes the CLI's execution mode (Dev vs Prod) like the
legacy entrypoint.

This module runs very early during CLI startup and must be quiet,
idempotent, and robust under headless/non-TTY conditions.

Recognized flags (duplicates allowed; last value wins):
- --headless               -> sets AWFL_NO_REPL=1 and removes the flag token(s)
- --skip-auth              -> sets SKIP_AUTH=1 and removes the flag token(s)
- --api-origin <url>       -> sets API_ORIGIN to <url> and removes both tokens
- --api-origin=<url>       -> same as above (trivial inline form support)

Additional behavior (parity with main_old._init_env_mode_from_argv):
- Detect a leading positional 'dev' to set WORKFLOW_ENV suffix 'Dev', update
  state dev mode (set_dev_mode(True)), set terminal title, and log the mode.
  Otherwise, set Prod mode (empty suffix) and title.
- Log the effective API origin, noting when overridden by API_ORIGIN.

Unknown flags are ignored. Missing values after --api-origin cause only the
flag token to be removed (no env change). All exceptions are swallowed to avoid
breaking startup.
"""
from __future__ import annotations

from typing import Optional
import os
import sys

import awfl.utils as wf_utils
from awfl.state import set_dev_mode


def init_env_mode_from_argv() -> None:
    """Detect and initialize execution mode from argv/environment.

    Behavior:
    - Scans sys.argv (in-place) for a small set of flags and removes them.
    - Updates os.environ accordingly. Overwriting is acceptable (explicit CLI override).
    - Duplicate flags are supported; last occurrence wins for values.
    - Unknown flags are left untouched.
    - Initializes Dev/Prod mode and logs API origin (legacy parity).
    - Never raises; exceptions are swallowed and treated as no-ops.

    Examples (conceptual):
    - ['awfl', 'dev'] -> WORKFLOW_ENV=='Dev', dev_mode on, title updated
    - ['awfl', '--headless'] -> env['AWFL_NO_REPL']=='1'; argv becomes ['awfl']
    - ['awfl', '--skip-auth', 'status'] -> env['SKIP_AUTH']=='1'; argv becomes ['awfl','status']
    - ['awfl','--api-origin','http://x','workflows'] -> env['API_ORIGIN']=='http://x'; argv becomes ['awfl','workflows']
    - ['awfl','--api-origin'] -> argv becomes ['awfl']; API_ORIGIN unchanged
    - ['awfl','--headless','--headless'] -> env set; both flags removed
    """
    try:
        argv = getattr(sys, 'argv', None)
        if not isinstance(argv, list) or not argv:
            return None  # Nothing to do

        # 1) Initialize mode (Dev/Prod) based on first positional arg
        try:
            positionals = [a for a in argv[1:] if isinstance(a, str) and a and not a.startswith('-')]
            is_dev = len(positionals) > 0 and str(positionals[0]).lower() == 'dev'
        except Exception:
            is_dev = False
        if is_dev:
            os.environ["WORKFLOW_ENV"] = "Dev"
            try:
                set_dev_mode(True)
            except Exception:
                pass
            wf_utils.log_unique("🏁 Starting awfl in Dev mode (WORKFLOW_ENV suffix 'Dev').")
            try:
                wf_utils.set_terminal_title("awfl [Dev]")
            except Exception:
                pass
        else:
            os.environ["WORKFLOW_ENV"] = ""
            try:
                set_dev_mode(False)
            except Exception:
                pass
            wf_utils.log_unique("🏁 Starting awfl in Prod mode (no WORKFLOW_ENV suffix).")
            try:
                wf_utils.set_terminal_title("awfl")
            except Exception:
                pass

        # 2) Scan/remove recognized flags and capture values
        new_argv = [argv[0]]
        headless_found = False
        skip_auth_found = False
        api_origin_value: Optional[str] = None

        i = 1
        # Single pass over argv, removing recognized flags and capturing values.
        while i < len(argv):
            tok = argv[i]

            # --headless
            if tok == '--headless':
                headless_found = True
                i += 1
                continue  # remove token

            # --skip-auth
            if tok == '--skip-auth':
                skip_auth_found = True
                i += 1
                continue  # remove token

            # --api-origin and --api-origin=<url>
            if tok == '--api-origin' or (isinstance(tok, str) and tok.startswith('--api-origin=') and len(tok) > len('--api-origin=')):
                # Inline form: --api-origin=<url>
                if isinstance(tok, str) and tok.startswith('--api-origin='):
                    val = tok.split('=', 1)[1]
                    # Accept non-empty values only.
                    if val:
                        api_origin_value = val
                    # Token removed regardless; proceed
                    i += 1
                    continue
                # Two-token form: --api-origin <url>
                next_val: Optional[str] = None
                if i + 1 < len(argv):
                    candidate = argv[i + 1]
                    # Treat a following flag-ish token as missing value.
                    if isinstance(candidate, str) and candidate and not candidate.startswith('-'):
                        next_val = candidate
                # If value present, record it and consume the value token.
                if next_val is not None:
                    api_origin_value = next_val
                    i += 2  # consume flag and value
                else:
                    i += 1  # consume flag only (missing value)
                continue  # remove consumed tokens

            # Unrecognized token: keep it.
            new_argv.append(tok)
            i += 1

        # Apply environment updates (last occurrence wins for values).
        if headless_found:
            os.environ['AWFL_NO_REPL'] = '1'
        if skip_auth_found:
            os.environ['SKIP_AUTH'] = '1'
        if api_origin_value is not None:
            os.environ['API_ORIGIN'] = api_origin_value

        # Mutate sys.argv in place to the filtered list.
        sys.argv[:] = new_argv

        # 3) After mode/env set, log effective API origin for transparency
        try:
            origin = wf_utils.get_api_origin()
            override = os.getenv("API_ORIGIN")
            if override:
                wf_utils.log_unique(f"🌐 API origin: {origin} (overridden by API_ORIGIN)")
            else:
                wf_utils.log_unique(f"🌐 API origin: {origin}")
        except Exception:
            pass
    except Exception:
        # Swallow any unexpected errors to keep startup robust.
        return None

    return None
