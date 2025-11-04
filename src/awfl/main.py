import os

import sys
import asyncio
import signal
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess
import difflib
from pathlib import Path
import uuid
import shlex

import awfl.utils as wf_utils
from awfl.auth import ensure_active_account
from awfl.response_handler import handle_response, set_session, get_session, get_latest_status
from awfl.utils import log_lines, log_unique, trigger_workflow
from pathspec import PathSpec
from awfl.commands import handle_command
# from consume_cli_operations import consume_cli_operations
from awfl.consumer import consume_events_sse
from awfl.state import set_workflow_env_suffix, get_active_workflow, normalize_workflow, DEFAULT_WORKFLOW

STATUS_URL = "http://localhost:5050/api/cli/status"

def _detect_git_root() -> str | None:
    """Return absolute path to git repo root, or None if not in a repo."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        )
        root = (res.stdout or "").strip()
        return root if root else None
    except Exception:
        return None


class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, queue, loop, git_root: str | None):
        self.queue = queue
        self.loop = loop
        self.git_root = git_root
        self.spec = self._load_gitignore(git_root) if git_root else PathSpec.from_lines("gitwildmatch", [])
        # Built-in safe ignores to avoid churn even if not listed in .gitignore
        self._default_ignored_prefixes = (
            ".git/", ".git", ".idea/", "node_modules/", ".venv/", "venv/", "__pycache__/"
        )

    def _load_gitignore(self, git_root: str):
        try:
            gitignore_path = os.path.join(git_root, ".gitignore")
            patterns = []
            if os.path.exists(gitignore_path):
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                    patterns = f.read().splitlines()
            return PathSpec.from_lines("gitwildmatch", patterns)
        except Exception as e:
            log_unique(f"⚠️ Failed to load .gitignore: {e}")
            return PathSpec.from_lines("gitwildmatch", [])

    def _is_under_root(self, path: str) -> bool:
        if not self.git_root:
            return False
        try:
            abs_path = str(Path(path).resolve())
            common = os.path.commonpath([abs_path, self.git_root])
            return common == self.git_root
        except Exception:
            return False

    def on_modified(self, event):
        if event.is_directory:
            return
        src = event.src_path
        if not os.path.isfile(src):
            return
        # Only process files inside the current git repo
        if not self._is_under_root(src):
            return
        if not self.is_ignored(src):
            asyncio.run_coroutine_threadsafe(self.queue.put(src), self.loop)

    def is_ignored(self, path: str) -> bool:
        # If no git root is known, ignore everything (watcher should be disabled, but double-guard here)
        if not self.git_root:
            return True
        try:
            relative_path = os.path.relpath(path, self.git_root)
            if relative_path.startswith(".."):
                return True
            # Built-in safe ignores
            rp = relative_path.replace("\\", "/")
            for pfx in self._default_ignored_prefixes:
                if rp == pfx.rstrip("/") or rp.startswith(pfx):
                    return True
            if self.spec.match_file(relative_path):
                return True
        except Exception:
            # Quietly ignore on errors to avoid log spam
            return True
        return False


def _read_text_utf8_ignore(p: str) -> str:
    try:
        with open(p, "rb") as f:
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def relative_to_git_root(filepath: str, git_root: str | None) -> str | None:
    if not git_root:
        return None
    try:
        return str(Path(filepath).resolve().relative_to(git_root))
    except Exception:
        return None


async def handle_file_updates(queue, git_root: str | None):
    while True:
        path = await queue.get()
        try:
            # Skip if outside git repo (defensive)
            git_path = relative_to_git_root(path, git_root)
            if not git_path:
                continue

            new_content = _read_text_utf8_ignore(path)

            # Read previous content from HEAD with binary-safe decoding
            old_content = ""
            try:
                if git_root:
                    result = subprocess.run(
                        ["git", "-C", git_root, "show", f"HEAD:{git_path}"],
                        capture_output=True,
                        text=False,
                        check=True,
                    )
                    old_content = (result.stdout or b"").decode("utf-8", errors="ignore")
            except subprocess.CalledProcessError:
                old_content = ""
            except Exception:
                old_content = ""

            diff = '\n'.join(difflib.unified_diff(
                (old_content or "").splitlines(),
                (new_content or "").splitlines(),
                fromfile="before",
                tofile="after",
                lineterm=""
            ))

            should_trigger = any(
                line.startswith("+") and " ai:" in line
                for line in diff.splitlines()
            )

            if should_trigger:
                log_unique("⚡️ Trigger condition matched (found relevant diff).")
                # Compute session as the base selected workflow name (no env suffix)
                session_id = _compute_session_workflow_name()
                # Pass base workflow name; environment suffixing handled centrally
                trigger_workflow("cli-CommentAdded", {
                    "sessionId": session_id,
                    "filepath": path,
                    "newContent": new_content,
                    "diff": diff
                })

        except Exception as e:
            # Keep this quiet and concise; noisy binary or non-repo files shouldn't spam logs
            log_unique(f"⚠️ Error processing change for {path}: {e}")


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


async def main():
    # Initialize session to the full selected workflow name (with env suffix)
    initial_session = _compute_session_workflow_name()
    set_session(initial_session)

    queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    # Register signal handlers to ensure dev cleanup on SIGINT/SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _dev_cleanup)
        except NotImplementedError:
            # add_signal_handler not supported (e.g., on Windows); rely on finally
            pass

    # Detect repo root; only watch files inside the repo
    git_root = _detect_git_root()
    observer = None

    if git_root:
        event_handler = FileChangeHandler(queue, loop, git_root)
        observer = Observer()
        observer.schedule(event_handler, git_root, recursive=True)
        observer.start()
        log_unique(f"👀 Watching repo for changes: {git_root}")
        asyncio.create_task(handle_file_updates(queue, git_root))
    else:
        log_unique("ℹ️ No git repository detected; file watcher for ' ai:' diffs is disabled.")

    ensure_active_account(prompt_login=True)

    # Start one project-wide SSE consumer (guarded by a local leader lock) and one session-scoped consumer
    asyncio.create_task(consume_events_sse(scope="project"))
    asyncio.create_task(consume_events_sse(scope="session"))

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
    asyncio.create_task(_refresh_prompt_task(session))

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
                text = await session.prompt_async(f"🤔 {prompt_wf} > ", rprompt=_rprompt)
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
        if observer is not None:
            observer.stop()
            observer.join()

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
