from __future__ import annotations
import asyncio
from typing import Optional


def attach_crash_on_consumer_exit(
    task: asyncio.Task,
    name: str,
    shutdown_evt: asyncio.Event,
    fatal: bool = True,
) -> None:
    """Attach a done-callback that logs a concise outcome and (optionally) signals shutdown.

    Regression fix: classify consumer outcomes to match legacy semantics (main_old.py):
    - On exception: project consumer is always fatal; others fatal when `fatal=True`.
    - On normal completion, inspect task.result():
      * project: 'skipped-lock' and 'cancelled' are non-fatal; any other status is fatal.
      * session/other: if `fatal` is True, treat as fatal; otherwise non-fatal.
    - If task was cancelled (CancelledError), treat as benign (non-fatal) for all.

    Logging is best-effort and must never raise.
    """

    # Construct a safe logger that prefers utils.log_unique, with silent fallbacks.
    def _make_logger():
        def _noop(_msg: str) -> None:
            return

        try:
            # Import lazily to avoid hard dependency at module import time.
            from awfl.utils import log_unique as _log_unique  # type: ignore

            def _log(msg: str) -> None:
                try:
                    _log_unique(msg)
                except Exception:
                    # Fallback to a plain print; still swallow any issues.
                    try:
                        print(msg, flush=True)
                    except Exception:
                        pass

            return _log
        except Exception:
            # utils not available; fallback to print, but keep it guarded.
            def _log(msg: str) -> None:
                try:
                    print(msg, flush=True)
                except Exception:
                    pass

            return _log

    _log = _make_logger()

    def _on_done(t: asyncio.Task) -> None:
        # Best-effort: determine status and log once; never raise from here.
        try:
            # First, attempt to read the exception (if any). If the task was cancelled, treat as benign.
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                # Treat cancel as benign (likely shutdown path)
                _log(f"ℹ️ {name} SSE consumer cancelled during shutdown (non-fatal).")
                return
            except Exception as e:
                # Could not retrieve exception cleanly; consider this an unexpected failure
                exc = e

            if exc is not None:
                # Any unhandled exception in a consumer is fatal for project; for others, respect `fatal`.
                if fatal or name == "project":
                    _log(f"❌ {name} SSE consumer ended with error: {exc}")
                    try:
                        shutdown_evt.set()
                    except Exception:
                        pass
                else:
                    _log(f"⚠️ {name} SSE consumer ended with error (non-fatal): {exc}")
                return

            # No exception -> inspect the returned status to classify outcome
            try:
                status = t.result()
            except asyncio.CancelledError:
                # Treat cancel as benign
                _log(f"ℹ️ {name} SSE consumer cancelled during shutdown (non-fatal).")
                return
            except Exception as e:
                _log(f"❌ {name} SSE consumer ended unexpectedly (could not read result: {e}).")
                try:
                    shutdown_evt.set()
                except Exception:
                    pass
                return

            # Project consumer: 'skipped-lock' and 'cancelled' are benign; others are fatal
            if name == "project":
                if status in ("skipped-lock", "cancelled"):
                    if status == "skipped-lock":
                        _log(
                            "ℹ️ project SSE consumer ended (non-fatal): another instance holds the project leader lock; continuing without project-wide execution in this terminal."
                        )
                    else:
                        _log("ℹ️ project SSE consumer cancelled during shutdown (non-fatal).")
                    return
                _log(f"❌ project SSE consumer ended unexpectedly (status={status!r}). Exiting.")
                try:
                    shutdown_evt.set()
                except Exception:
                    pass
                return

            # Session (or other) consumer: any normal completion is fatal if caller marked it fatal
            if fatal:
                _log(f"❌ {name} SSE consumer ended (status={status!r}).")
                try:
                    shutdown_evt.set()
                except Exception:
                    pass
            else:
                _log(f"ℹ️ {name} SSE consumer ended (non-fatal, status={status!r}).")
        except Exception:
            # Swallow anything that goes wrong in the callback path.
            pass

    try:
        task.add_done_callback(_on_done)
    except Exception:
        # If we cannot attach the callback, still attempt to handle the terminal state.
        try:
            if task.done():
                _on_done(task)
            elif fatal:
                # We cannot observe the outcome later; request shutdown proactively.
                try:
                    shutdown_evt.set()
                except Exception:
                    pass
        except Exception:
            # Swallow anything that goes wrong in the fallback path.
            pass
