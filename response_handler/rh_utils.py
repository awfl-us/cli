import time
from datetime import datetime
from urllib.parse import urlparse


def mask_headers(headers: dict) -> dict:
    masked = dict(headers)
    if "Authorization" in masked:
        masked["Authorization"] = "Bearer ***"
    return masked


def ts_to_ms(ts: str | None) -> int:
    if not ts:
        return 0
    s = str(ts).strip()
    try:
        # Handle common ISO8601 with Z or offset
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except Exception:
        # Fallback: try numeric seconds
        try:
            return int(float(s) * 1000)
        except Exception:
            return 0


def is_background_from_payload(data: dict) -> bool:
    # Primary signal
    if bool(data.get("background") is True):
        return True
    # Legacy compatibility: callback_session starting with "background-"
    cb_sess = str(data.get("callback_session") or "")
    return cb_sess.startswith("background-")


def read_file_text_utf8_ignore(filepath: str) -> str:
    with open(filepath, "rb") as f:
        data = f.read()
    return data.decode("utf-8", errors="ignore")


def _scan_quote_state(s: str) -> tuple[bool, bool]:
    """Lightweight shell-like scanner to determine if we're left inside a
    single-quoted or double-quoted context at end of string.

    - Single quotes are literal until the next single quote.
    - Double quotes can be escaped with backslash outside single quotes.
    - Backslash escaping is ignored inside single quotes (Bash semantics).
    Returns (in_single, in_double).
    """
    in_single = False
    in_double = False
    escape = False
    for ch in s:
        if escape:
            # Current char is escaped; consume and clear escape.
            escape = False
            continue
        if in_single:
            if ch == "'":
                in_single = False
            # Backslash is literal in single quotes; no escaping.
            continue
        if in_double:
            if ch == '"':
                in_double = False
                continue
            if ch == "\\":
                # Only in double or unquoted, backslash can escape next char
                escape = True
                continue
            continue
        # Unquoted context
        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue
        if ch == "\\":
            escape = True
            continue
    return in_single, in_double


def sanitize_shell_command(cmd: str) -> tuple[str, str | None]:
    """Best-effort cleanup of common LLM artifacts before shell execution.

    - Balances unmatched single/double quotes by appending a closing quote when needed.
    - Removes a trailing unmatched single quote (') if present at the very end.
    - Removes extra trailing right curly brace(s) if they appear unmatched.
    Returns (sanitized_cmd, change_reason or None).
    """
    original = cmd
    s = (cmd or "").rstrip()
    reasons: list[str] = []

    # 1) Remove a lone trailing unmatched single quote (common hallucination)
    if s.endswith("'"):
        in_single, in_double = _scan_quote_state(s)
        # If we're NOT actually inside a single-quoted region (i.e., this last quote is extra), drop it
        # Example: "echo hi'" -> "echo hi"
        if not in_single and not in_double:
            # Count raw quotes as a fallback guard
            if s.count("'") % 2 == 1:
                s = s[:-1].rstrip()
                reasons.append("removed trailing unmatched '")

    # 2) Balance quotes if we end inside a quoted region
    in_single, in_double = _scan_quote_state(s)
    if in_single:
        s = s + "'"
        reasons.append("added missing closing '")
        # After appending, we are no longer inside single quotes
        in_single = False
    if in_double:
        s = s + '"'
        reasons.append('added missing closing "')
        in_double = False

    # 3) Fix trailing unmatched right curly braces '}' added accidentally
    # Only remove if there are more closing braces than opening ones overall.
    while s.endswith("}") and (s.count("}") > s.count("{")):
        s = s[:-1].rstrip()
        reasons.append("removed trailing unmatched }")

    return (s, "; ".join(reasons) or None) if s != original else (original, None)
