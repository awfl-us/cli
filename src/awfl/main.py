import os
import sys
import asyncio
import signal
import contextlib
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout

from awfl.auth import ensure_active_account
from awfl.response_handler import set_session
from awfl.utils import log_lines, log_unique, trigger_workflow
from awfl.commands import handle_command
from awfl.consumer import consume_events_sse
from awfl.state import set_workflow_env_suffix, get_active_workflow, normalize_workflow, DEFAULT_WORKFLOW
from awfl.onboarding.git import first_run_git_onboarding
from awfl.bootstrap.args import process_run_alias_from_argv, startup_command_from_argv, is_long_running_startup
from awfl.bootstrap.env import init_env_mode_from_argv
from awfl.consumer.supervisor import attach_crash_on_consumer_exit
from awfl.ui.prompt import rprompt, refresh_prompt_task
from awfl.session.workflow import compute_session_workflow_name
from awfl.dev.cleanup import dev_cleanup


def _should_prompt_login() -> bool:
    """Return True if we should trigger interactive Google login on startup.
    Respect env-based auth overrides to avoid prompting when FIREBASE_CUSTOM_TOKEN or
    FIREBASE_ID_TOKEN (or SKIP_AUTH) are set.
    """
    if os.getenv("SKIP_AUTH") == "1":
        return False
    if os.getenv("FIREBASE_ID_TOKEN"):
        return False
    if os.getenv("FIREBASE_CUSTOM_TOKEN"):
        return False
    return True


def _is_headless() -> bool:
    if os.getenv("AWFL_NO_REPL") == "1":
        return True
    term = os.getenv("TERM", "").strip()
    try:
        is_tty = bool(sys.stdout.isatty())
    except Exception:
        is_tty = False
    return (not term) or (not is_tty)


async def main():
    # Initialize session to the full selected workflow name (with env suffix)
    initial_session = compute_session_workflow_name()
    set_session(initial_session)

    loop = asyncio.get_event_loop()

    # Register signal handlers to ensure dev cleanup on SIGINT/SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, dev_cleanup)
        except NotImplementedError:
            # add_signal_handler not supported (e.g., on Windows); rely on finally
            pass

    # Only prompt for Google Device Flow if no env-based auth is configured
    if _should_prompt_login():
        ensure_active_account(prompt_login=True)

    # Run first-run git onboarding before starting SSE consumers so prompts render cleanly
    if not _is_headless():
        first_run_git_onboarding()

    # Start one project-wide SSE consumer (guarded by a local leader lock) and one session-scoped consumer
    consumer_shutdown_evt = asyncio.Event()
    project_consumer = asyncio.create_task(consume_events_sse(scope="project"), name="sse-project")
    session_consumer = asyncio.create_task(consume_events_sse(scope="session"), name="sse-session")
    # Only the session-scoped consumer is fatal. The project-scoped consumer may exit early
    # (e.g., lock held by another terminal) and should not cause the CLI to shut down.
    attach_crash_on_consumer_exit(project_consumer, "project", consumer_shutdown_evt, fatal=False)
    attach_crash_on_consumer_exit(session_consumer, "session", consumer_shutdown_evt, fatal=True)

    # If a long-running startup command was provided, execute it without hopping threads,
    # so any dev watcher can create asyncio tasks on this event loop safely.
    bootstrap_cmd = os.environ.pop("AWFL_BOOTSTRAP_CMD", None)
    if bootstrap_cmd:
        async def _run_bootstrap():
            try:
                handle_command(bootstrap_cmd)
                # Keep session aligned after potential workflow changes
                set_session(compute_session_workflow_name())
            except Exception as e:
                log_unique(f"⚠️ Error executing startup command '{bootstrap_cmd}': {e}")
        asyncio.create_task(_run_bootstrap(), name="bootstrap-cmd")

    # Headless mode: no interactive REPL; just run the SSE consumers and wait
    if _is_headless():
        log_unique("🧪 Running in headless mode (no REPL): SSE consumers active.")
        try:
            await consumer_shutdown_evt.wait()
            log_unique("❌ Event stream consumer stopped. Exiting CLI so your supervisor can restart it.")
            # Best-effort cancel the other consumer (if still running)
            for t in (project_consumer, session_consumer):
                if not t.done():
                    t.cancel()
            await asyncio.sleep(0.05)
            sys.exit(2)
        except KeyboardInterrupt:
            pass
        finally:
            for t in (project_consumer, session_consumer):
                if t and not t.done():
                    t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    if t:
                        await t
        return

    # Interactive REPL path
    session = PromptSession()
    # Kick off periodic UI refresh so rprompt reflects current status during idle
    refresh_task = asyncio.create_task(refresh_prompt_task(session), name="refresh-rprompt")

    # Background waiter that resolves when any fatal consumer has ended
    consumer_waiter = asyncio.create_task(consumer_shutdown_evt.wait(), name="consumer-waiter")

    try:
        with patch_stdout():
            while True:
                # Keep the response handler session aligned with current selection
                set_session(compute_session_workflow_name())

                os.system('clear')
                for line in log_lines:
                    print_formatted_text(line)
                # Show selected workflow; status is rendered live on the right via rprompt
                active_wf = get_active_workflow() or DEFAULT_WORKFLOW
                prompt_wf = normalize_workflow(active_wf)

                prompt_task = asyncio.create_task(
                    session.prompt_async(f"🧐 {prompt_wf} > ", rprompt=rprompt),
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
                    set_session(compute_session_workflow_name())
                    continue
                workflow = get_active_workflow() or DEFAULT_WORKFLOW
                workflow = normalize_workflow(workflow)
                session_id = compute_session_workflow_name()
                # Log base workflow name; utils.trigger_workflow will handle env suffixing per mode
                log_unique(f"🚀 {session_id} > {text}")
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
    # Recognize 'run'/'headless'/'remote' aliases before any other parsing
    process_run_alias_from_argv()

    init_env_mode_from_argv()
    # If a startup command was provided (e.g., "awfl dev start" or "awfl help"),
    # run it non-interactively and either keep the CLI open (for long-running dev commands)
    # or exit after execution (for short-lived commands).
    _startup_cmd = startup_command_from_argv()
    if _startup_cmd:
        if is_long_running_startup(_startup_cmd):
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
