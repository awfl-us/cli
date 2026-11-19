from __future__ import annotations


def dev_cleanup() -> None:
    """Best-effort developer cleanup hook.

    Invoked during CLI shutdown/signal handling to tear down any developer
    runtime helpers that may have been started by this session (e.g., file
    watchers, docker compose, ngrok). This function is intentionally quiet and
    idempotent:
    - Safe under headless/non-TTY environments.
    - Never raises; all exceptions are swallowed.
    - No interactive prompts or logging on normal paths.
    """
    try:
        # Import lazily so the CLI can run even if dev subcommands are not
        # available (e.g., end-user installs without dev extras).
        from awfl.cmds.dev.subcommands.stop import stop_dev  # type: ignore

        # Call with an argv-like empty list; stop_dev is expected to perform
        # best-effort teardown and also be safe when nothing is running.
        stop_dev([])
    except Exception:
        # Best-effort only: swallow all exceptions to avoid interfering with
        # shutdown (ImportError, AttributeError, or runtime errors inside stop).
        pass
