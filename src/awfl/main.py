import os
import sys
import asyncio
import signal
import contextlib
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout
import shlex

import awfl.utils as wf_utils
from awfl.auth import ensure_active_account
from awfl.response_handler import set_session, get_latest_status
from awfl.utils import log_lines, log_unique, trigger_workflow
from awfl.commands import handle_command
from awfl.consumer import consume_events_sse
from awfl.state import set_workflow_env_suffix, get_active_workflow, normalize_workflow, DEFAULT_WORKFLOW


def _compute_session_workflow_name() -> str:
    override = os.environ.get("ASSISTANT_WORKFLOW")
    if override:
        return normalize_workflow(override)
    active_wf = normalize_workflow(get_active_workflow() or DEFAULT_WORKFLOW)
    return active_wf


def _dev_cleanup():
    """Attempt to shut down dev resources (watcher, docker compose, ngrok)."""
    try:
        from awfl.cmds.dev.subcommands.stop import stop_dev
        stop_dev([])
    except Exception:
        # Best-effort cleanup
        pass


def _rprompt():
    # Dynamic right-side status: updates when the app is invalidated
    status, _err = get_latest_status()
    return f"({status})" if status else ""


async def _refresh_prompt_task(session: PromptSession):
    # Periodically invalidate the UI so rprompt reflects current status during idle
    while True:
        await asyncio.sleep(0.5)
        try:
            session.app.invalidate()
        except Exception:
            pass


def _argv_positionals() -> list[str]:
    return [a for a in sys.argv[1:] if a and not a.startswith("-")]


def _argv_all() -> list[str]:
    return sys.argv[1:]


def _init_env_mode_from_argv():
    # Detect "awfl dev" (or python cli/main.py dev). Default is prod (no suffix)
    args = _argv_positionals()
    is_dev = len(args) > 0 and args[0].lower() == "dev"
    if is_dev:
        # Child processes and utils will read WORKFLOW_ENV
        os.environ["WORKFLOW_ENV"] = "Dev"
        set_workflow_env_suffix("Dev")
        wf_utils.log_unique("🏁 Starting awfl in Dev mode (WORKFLOW_ENV suffix 'Dev').")
        try:
            wf_utils.set_terminal_title("awfl [Dev]")
        except Exception:
            pass
    else:
        os.environ["WORKFLOW_ENV"] = ""
        wf_utils.log_unique("🏁 Starting awfl in Prod mode (no WORKFLOW_ENV suffix).")
        try:
            wf_utils.set_terminal_title("awfl")
        except Exception:
            pass

    # After mode set, log the effective API origin and execution mode for transparency
    origin = wf_utils.get_api_origin()
    exec_mode = os.getenv("WORKFLOW_EXEC_MODE", "api").lower()
    override = os.getenv("API_ORIGIN")
    if override:
        wf_utils.log_unique(f"🌐 API origin: {origin} (overridden by API_ORIGIN)")
    else:
        wf_utils.log_unique(f"🌐 API origin: {origin}")
    # wf_utils.log_unique(f"🧭 Execution mode: {exec_mode}")


def _startup_command_from_argv() -> str | None:
    """Return a startup command line to execute, if provided.
    Rules:
    - No args -> None (enter interactive)
    - ['dev'] -> None (enter interactive in Dev mode)
    - Anything else -> join all args and run via handle_command
    """
    all_args = _argv_all()
    if not all_args:
        return None
    if len(all_args) == 1 and all_args[0].lower() == "dev":
        return None
    # Reconstruct a shell-style command string so router can split consistently
    try:
        return shlex.join(all_args)
    except Exception:
        return " ".join(all_args)


def _is_long_running_startup(cmd: str) -> bool:
    try:
        parts = shlex.split(cmd)
    except Exception:
        parts = cmd.split()
    if not parts:
        return False
    # Identify commands that start long-lived dev processes/watchers
    if parts[0] == "dev" and len(parts) > 1 and parts[1] in ("start", "watch"):
        return True
    return False


def _attach_crash_on_consumer_exit(task: asyncio.Task, name: str, evt: asyncio.Event, *, fatal: bool):
    def _cb(t: asyncio.Task):
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            # If cancelled during shutdown, don't trigger the event
            return
        except Exception as e:
            # If we cannot access exception(), treat as unknown
            exc = e

        if exc:
            # Any unhandled exception in a consumer is fatal for its classification
            if fatal:
                wf_utils.log_unique(f"❌ {name} SSE consumer ended with error: {exc}")
                evt.set()
            else:
                wf_utils.log_unique(f"⚠️ {name} SSE consumer ended with error (non-fatal): {exc}")
            return

        # Normal completion (e.g., project consumer skipped due to leader lock)
        if fatal:
            wf_utils.log_unique(f"❌ {name} SSE consumer ended.")
            evt.set()
        else:
            wf_utils.log_unique(f"ℹ️ {name} SSE consumer ended (non-fatal).")

    task.add_done_callback(_cb)


async def main():
    # Initialize session to the full selected workflow name (with env suffix)
    initial_session = _compute_session_workflow_name()
    set_session(initial_session)

    loop = asyncio.get_event_loop()

    # Register signal handlers to ensure dev cleanup on SIGINT/SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _dev_cleanup)
        except NotImplementedError:
            # add_signal_handler not supported (e.g., on Windows); rely on finally
            pass

    ensure_active_account(prompt_login=True)

    # Start one project-wide SSE consumer (guarded by a local leader lock) and one session-scoped consumer
    consumer_shutdown_evt = asyncio.Event()
    project_consumer = asyncio.create_task(consume_events_sse(scope="project"), name="sse-project")
    session_consumer = asyncio.create_task(consume_events_sse(scope="session"), name="sse-session")
    _attach_crash_on_consumer_exit(project_consumer, "project", consumer_shutdown_evt, fatal=False)
    _attach_crash_on_consumer_exit(session_consumer, "session", consumer_shutdown_evt, fatal=True)

    # If a long-running startup command was provided, execute it now inside the running event loop
    bootstrap_cmd = os.environ.pop("AWFL_BOOTSTRAP_CMD", None)
    if bootstrap_cmd:
        try:
            handle_command(bootstrap_cmd)
            # Keep session aligned after potential workflow changes
            set_session(_compute_session_workflow_name())
        except Exception as e:
            log_unique(f"⚠️ Error executing startup command '{bootstrap_cmd}': {e}")

    session = PromptSession()
    # Kick off periodic UI refresh so rprompt reflects current status during idle
    refresh_task = asyncio.create_task(_refresh_prompt_task(session), name="refresh-rprompt")

    # Background waiter that resolves when any fatal consumer has ended
    consumer_waiter = asyncio.create_task(consumer_shutdown_evt.wait(), name="consumer-waiter")

    try:
        with patch_stdout():
            while True:
                # Keep the response handler session aligned with current selection
                set_session(_compute_session_workflow_name())

                os.system('clear')
                for line in log_lines:
                    print_formatted_text(line)
                # Show selected workflow; status is rendered live on the right via rprompt
                active_wf = get_active_workflow() or DEFAULT_WORKFLOW
                prompt_wf = normalize_workflow(active_wf)

                prompt_task = asyncio.create_task(
                    session.prompt_async(f"🧐 {prompt_wf} > ", rprompt=_rprompt),
                    name="prompt"
                )

                done, pending = await asyncio.wait(
                    {prompt_task, consumer_waiter}, return_when=asyncio.FIRST_COMPLETED
                )

                # If a fatal consumer ended, crash the CLI (bad UX to stay open without streams)
                if consumer_waiter in done:
                    # Cancel the prompt if still waiting
                    if not prompt_task.done():
                        prompt_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await prompt_task
                    log_unique("❌ Event stream consumer stopped. Exiting CLI so you can restart.")
                    # Best-effort cancel the other consumer (if still running)
                    for t in (project_consumer, session_consumer):
                        if not t.done():
                            t.cancel()
                    await asyncio.sleep(0.05)
                    # Exit with a non-zero code so wrappers can auto-restart if desired
                    sys.exit(2)

                # Otherwise, handle the prompt result
                try:
                    text = prompt_task.result()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log_unique(f"⚠️ Prompt error: {e}")
                    continue

                if text.lower() == "exit":
                    break
                if handle_command(text):
                    # After commands (like switching workflows), update session to match
                    set_session(_compute_session_workflow_name())
                    continue
                workflow = get_active_workflow() or DEFAULT_WORKFLOW
                workflow = normalize_workflow(workflow)
                session_id = _compute_session_workflow_name()
                # Log base workflow name; utils.trigger_workflow will handle env suffixing per mode
                log_unique(f"🚀 Query submitted to {workflow} (session: {session_id}): {text}")
                # Pass base workflow name; env suffixing handled centrally in utils.trigger_workflow
                trigger_workflow(workflow, {
                    "sessionId": session_id,
                    "query": text
                })
    except KeyboardInterrupt:
        pass
    finally:
        # Cleanup: cancel background tasks and suppress CancelledError to avoid noisy tracebacks
        for t in (project_consumer, session_consumer, consumer_waiter, refresh_task):
            if t and not t.done():
                t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                if t:
                    await t


if __name__ == "__main__":
    _init_env_mode_from_argv()
    # If a startup command was provided (e.g., "awfl dev start" or "awfl help"),
    # run it non-interactively and either keep the CLI open (for long-running dev commands)
    # or exit after execution (for short-lived commands).
    _startup_cmd = _startup_command_from_argv()
    if _startup_cmd:
        if _is_long_running_startup(_startup_cmd):
            # Defer execution into the async runtime and keep CLI open
            os.environ["AWFL_BOOTSTRAP_CMD"] = _startup_cmd
            asyncio.run(main())
            sys.exit(0)
        else:
            try:
                handled = handle_command(_startup_cmd)
                # Flush any buffered log lines to stdout
                for line in log_lines:
                    try:
                        print(line)
                    except Exception:
                        pass
            finally:
                # Exit for short-lived commands
                sys.exit(0)

    asyncio.run(main())
