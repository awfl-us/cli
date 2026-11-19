from __future__ import annotations
"""
Phase 0 memo (short)
- Assumptions: response_handler.get_latest_status() may return a tuple
  (status, err) or a status-like value, and can raise during early startup.
  PromptSession.app.invalidate() can raise if the session/app is not
  fully constructed or is shutting down. This module loads during startup
  and must be safe under headless/non-TTY and never block or prompt.
- Key behaviors: rprompt() returns a concise right-side status string from
  the latest status, empty if none/unavailable. refresh_prompt_task()
  invalidates ~every 0.5s to refresh rprompt and tolerates lifecycle errors.
- Edge cases: get_latest_status missing/raises; returns None/empty; returns
  long text; PromptSession has no .app or it's closing; task cancellation.
  Importing under headless (AWFL_NO_REPL=1) has no side effects. Both
  functions swallow exceptions and default to safe no-ops/empty strings.
- Quick tests:
  - python -c "import sys; sys.path.insert(0, 'src'); import awfl.ui.prompt as p; print('r:', p.rprompt()); print('ok')"
  - In an async REPL, create a PromptSession and run refresh_prompt_task()
    for ~1s, then cancel; no exceptions should surface.
"""

import asyncio
from typing import Any, Iterable

try:
    # Prefer importing at module load, but tolerate failures.
    from awfl.response_handler import get_latest_status as _get_latest_status  # type: ignore
except Exception:  # pragma: no cover - defensive
    _get_latest_status = None  # type: ignore

from prompt_toolkit import PromptSession


def rprompt() -> str:
    """Return a concise right-aligned status string for the REPL.

    Behavior:
    - Queries response_handler.get_latest_status() if available.
    - Accepts either a tuple/list (status, err) or a plain status value.
    - Returns "(status)" when status is truthy; otherwise returns "".
    - Swallows all exceptions. No truncation to preserve legacy behavior.
    """
    try:
        if _get_latest_status is None:
            return ""

        result: Any = _get_latest_status()  # May be tuple or plain value.
        # Unpack common shapes: (status, err) or [status, err]
        if isinstance(result, (tuple, list)):
            status = result[0] if result else None
        else:
            status = result

        if not status:
            return ""

        s = str(status)
        if not s:
            return ""

        return f"({s})"
    except Exception:  # pragma: no cover - defensive
        # Any issue (import-time or call-time): render nothing.
        return ""


async def refresh_prompt_task(session: PromptSession) -> None:
    """Periodically invalidate the UI so rprompt refreshes during idle.

    - Runs at ~0.5s cadence.
    - Swallows lifecycle-related exceptions (session/app closing, etc.).
    - Exits cleanly on asyncio.CancelledError.
    """
    try:
        while True:
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                # Graceful cancellation: exit the task quietly.
                break

            try:
                app = getattr(session, "app", None)
                if app is None:
                    continue
                # invalidate() may raise if the app is closing; ignore.
                app.invalidate()
            except Exception:
                # Ignore transient UI errors to avoid user-facing noise.
                pass
    except Exception:  # pragma: no cover - defensive
        # Never propagate exceptions from a background UI task.
        return
