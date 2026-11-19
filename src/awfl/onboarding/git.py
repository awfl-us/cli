"""First-run Git/GitHub onboarding utilities.

Quiet, best-effort onboarding invoked early during CLI startup (before SSE starts)
so any user-facing hints render cleanly. The function must be:
- Fast and idempotent.
- Skipped entirely in headless environments (no TTY or AWFL_NO_REPL) and in CI.
- Non-fatal: never raise; swallow all exceptions.

Expanded implementation:
- If git is missing, attempt a best-effort install (brew/apt/dnf/winget/choco). Never prompt for sudo.
- Ensure a local repo exists (git init) with a main branch and an initial commit if none.
- Detect whether any remotes exist.
- If no remotes, prefer GitHub CLI (gh) to create a private repo and push; otherwise log clear manual steps.
- When no git repo is present at startup, prompt for a project name (interactive only) to use as the default
  GitHub repository name.
- Enforce n00b local mode: if git was missing at function entry, do not attempt GitHub auth/push in this run
  even if an installation later succeeds (but still init the local repo/commit when possible).
- Opt-out via AWFL_SKIP_GIT_ONBOARDING=1.

Added: Project bootstrap without remote
- When no git repo existed at startup (no_repo_before == True), after prompting for the name we
  create a new AWFL project via the API WITHOUT specifying a remote and we write repo-local
  .awfl/config.json with { projectId, name }. Errors are logged and ignored.
"""
from __future__ import annotations

import configparser
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Optional, Tuple

import requests

from awfl.utils import log_unique, get_api_origin
from awfl.auth import get_auth_headers, set_project_id


# ------------------------
# Helpers: environment
# ------------------------

def _is_headless() -> bool:
    """Headless detection aligned with main.py.

    Criteria:
    - AWFL_NO_REPL == "1" OR
    - TERM missing/empty OR
    - stdout is not a TTY
    """
    if os.getenv("AWFL_NO_REPL") == "1":
        return True
    term = os.getenv("TERM", "").strip()
    try:
        is_tty = bool(sys.stdout.isatty())
    except Exception:
        is_tty = False
    return (not term) or (not is_tty)


def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 60) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 127, "", str(e)


def _prompt_project_name(default_name: str) -> str:
    """Prompt the user for a project/repo name; fall back to default when blank or on error."""
    try:
        raw = input(f"Project name (used for GitHub repo) [{default_name}]: ").strip()
        return raw or default_name
    except Exception:
        return default_name


# ------------------------
# Git/local repo helpers
# ------------------------

def _has_git_repo(cwd: Path) -> bool:
    return (cwd / ".git").is_dir()


def _git_available() -> bool:
    return which("git") is not None


def _ensure_local_repo_initialized(cwd: Path) -> None:
    """Initialize a local git repo with a 'main' branch and at least one commit.

    Idempotent and non-fatal.
    """
    # Initialize if missing
    if not _has_git_repo(cwd):
        # Prefer modern 'git init -b main' if available; fall back otherwise
        code, _, _ = _run(["git", "init", "-b", "main"], cwd)
        if code != 0:
            _run(["git", "init"], cwd)
            # For brand-new repo with no HEAD, assign main
            _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd)

    # Ensure default branch named 'main' (works both with/without commits)
    code, _, _ = _run(["git", "rev-parse", "--verify", "HEAD"], cwd)
    has_commits = (code == 0)
    if has_commits:
        # If repository already has commits, ensure branch exists and is checked out as 'main'
        _run(["git", "checkout", "-B", "main"], cwd)
    else:
        # No commits yet; ensure HEAD points at main
        _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd)

    # Create initial commit if there are none
    code, _, _ = _run(["git", "rev-parse", "--verify", "HEAD"], cwd)
    if code != 0:
        # Stage everything if there is any content
        _run(["git", "add", "-A"], cwd)
        # Check if there is anything staged or untracked to commit
        s_code, out, _ = _run(["git", "status", "--porcelain"], cwd)
        if s_code == 0 and not out.strip():
            # Absolutely empty tree -> allow-empty initial commit
            _run(["git", "commit", "--allow-empty", "-m", "Initial commit"], cwd)
        else:
            _run(["git", "commit", "-m", "Initial commit"], cwd)


def _count_git_remotes_via_config(cwd: Path) -> int:
    cfg_path = cwd / ".git" / "config"
    if not cfg_path.is_file():
        return 0
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg_path)
    except Exception:
        return 0
    n = 0
    for section in parser.sections():
        if section.startswith("remote "):
            n += 1
    return n


def _has_remote(cwd: Path) -> bool:
    # Try robust parse first
    if _count_git_remotes_via_config(cwd) > 0:
        return True
    # Fallback to git remote
    code, out, _ = _run(["git", "remote"], cwd)
    if code == 0 and out.strip():
        return True
    return False


# ------------------------
# Installation helpers
# ------------------------

def _try_install_git() -> None:
    """Best-effort attempt to install git. Never prompts for sudo; log-only on failure.
    """
    try:
        if _git_available():
            return
        system = platform.system().lower()
        # macOS
        if system == "darwin":
            if which("brew"):
                log_unique("Installing git via Homebrew …")
                _run(["brew", "install", "git"], cwd=None, timeout=10 * 60)
            else:
                log_unique("Git not found. Install Homebrew (https://brew.sh) then run: brew install git")
            return
        # Windows
        if system == "windows":
            if which("winget"):
                log_unique("Installing Git via winget …")
                _run(["winget", "install", "--id", "Git.Git", "-e", "--source", "winget", "--silent"], timeout=15 * 60)
            elif which("choco"):
                log_unique("Installing Git via Chocolatey …")
                _run(["choco", "install", "git", "-y"], timeout=15 * 60)
            else:
                log_unique("Git not found. Install via winget/choco or download from https://git-scm.com/download/win")
            return
        # Linux
        apt = which("apt-get") or which("apt")
        dnf = which("dnf")
        yum = which("yum")
        pacman = which("pacman")
        if apt:
            log_unique("Attempting git install via apt-get (no sudo prompts) …")
            _run(["bash", "-lc", "sudo -n apt-get update && sudo -n apt-get install -y git"], timeout=15 * 60)
        elif dnf:
            log_unique("Attempting git install via dnf (no sudo prompts) …")
            _run(["bash", "-lc", "sudo -n dnf install -y git"], timeout=15 * 60)
        elif yum:
            log_unique("Attempting git install via yum (no sudo prompts) …")
            _run(["bash", "-lc", "sudo -n yum install -y git"], timeout=15 * 60)
        elif pacman:
            log_unique("Attempting git install via pacman …")
            _run(["bash", "-lc", "sudo -n pacman -S --noconfirm git"], timeout=15 * 60)
        else:
            log_unique("Git not found. Install it via your package manager (apt/dnf/yum/pacman) or from https://git-scm.com/download/linux")
    except Exception:
        # Silent best-effort
        pass


# ------------------------
# GitHub helpers
# ------------------------

def _sanitize_repo_name(raw: str) -> str:
    s = raw.strip().lower()
    # Replace spaces/underscores with dashes, drop invalid chars
    s = s.replace(" ", "-").replace("_", "-")
    s = re.sub(r"[^a-z0-9.-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-.")
    return s or "repo"


def _gh_authed() -> bool:
    if not which("gh"):
        return False
    code, _, _ = _run(["gh", "auth", "status", "-h", "github.com", "--quiet"], timeout=10)
    return code == 0


def _attempt_github_push(cwd: Path, *, repo_name: str, visibility: str = "private") -> None:
    """Try to create a GitHub repo via gh and push. Log instructions on failure.

    Idempotent: if origin exists, this is a no-op. If gh is unavailable or unauthenticated,
    emit manual steps.
    """
    if _has_remote(cwd):
        return

    visible_flag = "--private" if visibility != "public" else "--public"

    if which("gh") and _gh_authed():
        log_unique("No Git remote detected — creating one on GitHub via gh and pushing …")
        # gh handles existing repos gracefully by erroring; we'll fall through to guidance
        # Use --remote=origin so subsequent git push/pull use origin
        code, out, err = _run([
            "gh", "repo", "create", repo_name,
            "--source", ".",
            visible_flag,
            "--remote", "origin",
            "--push",
        ], cwd)
        if code == 0:
            log_unique("✅ GitHub repo created and 'origin' set. Your code is pushed to GitHub.")
            return
        # If failed, log tail to aid debugging then print manual steps
        tail = (err or out or "").strip().splitlines()[-10:]
        if tail:
            log_unique("gh repo create failed:" + "\n" + "\n".join(tail))

    # Manual fallback
    log_unique(
        "🔧 To publish this repository:\n"
        f"- If you have GitHub CLI:  gh auth login  (then)  gh repo create {repo_name} --source=. --private --remote=origin --push\n"
        "- Or create a GitHub repo in your browser, then run:\n"
        "    git remote add origin <url>\n"
        "    git push -u origin main"
    )


# ------------------------
# AWFL project bootstrap helpers (no remote)
# ------------------------

def _write_repo_awfl_config(base: Path, project_id: str, name: Optional[str]) -> None:
    """Write or update .awfl/config.json with projectId and name.

    Non-fatal; ensures parent directory exists and writes atomically.
    """
    try:
        cfg_dir = base / ".awfl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "config.json"
        data = {}
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
        data["projectId"] = project_id
        if name:
            data["name"] = name
        tmp = cfg_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, cfg_path)
        log_unique(f"📝 Initialized .awfl/config.json with projectId={project_id}")
    except Exception as e:
        log_unique(f"⚠️ Couldn't write .awfl/config.json: {e}")


def _create_project_no_remote_sync(name: str) -> Optional[dict]:
    """Create a project via API without a remote, synchronously.

    Returns the project dict on success, otherwise None. Non-fatal.
    """
    try:
        origin = get_api_origin()
        url = f"{origin}/workflows/projects"
        headers = {"Content-Type": "application/json"}
        try:
            headers.update(get_auth_headers())
        except Exception:
            # Allow unauthenticated local dev when SKIP_AUTH=1
            pass
        body = {"name": name, "live": False}
        resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=20)
        if resp.status_code not in (200, 201):
            txt = (resp.text or "")[:300]
            log_unique(f"⚠️ Project create failed ({resp.status_code}): {txt}")
            return None
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("project"), dict):
            proj = payload["project"]
            pid = proj.get("id") or proj.get("projectId")
            if pid:
                log_unique(f"🆕 Created project: {pid} ({proj.get('name') or ''})")
                return proj
        return None
    except Exception as e:
        log_unique(f"⚠️ Error creating project: {e}")
        return None


# ------------------------
# Entry point
# ------------------------

def first_run_git_onboarding() -> None:
    """Best-effort onboarding for initializing git and creating/pushing a GitHub remote.

    Behavior:
    - Skip in CI/headless or when AWFL_SKIP_GIT_ONBOARDING=1.
    - If git missing, attempt install (non-interactive); remember this state.
    - If no local git repo exists, prompt for a project name (interactive) to use for the GitHub repo.
    - Ensure a local repo with main and an initial commit exists.
    - If a remote already exists, exit quietly.
    - If no remote and git existed at entry: attempt GitHub push via gh; otherwise show guidance.
    - Additionally, when this was a brand-new repo (no_repo_before=True), create an AWFL project via API
      without a remote and write .awfl/config.json with projectId and name.
    - Never raise on errors.
    """
    try:
        # Skip in CI/headless or when explicitly disabled
        if os.getenv("CI") == "1" or _is_headless() or os.getenv("AWFL_SKIP_GIT_ONBOARDING") == "1":
            return None

        cwd = Path.cwd()

        # Record whether git was present on entry (n00b local mode guard)
        had_git_on_entry = _git_available()
        if not had_git_on_entry:
            log_unique("🧰 Git not found. Attempting a best-effort installation …")
            _try_install_git()

        # Re-check after attempted install
        if not _git_available():
            # Can't proceed further; provide concise guidance for the platform
            sys_name = platform.system()
            if sys_name == "Darwin":
                log_unique("❗ Git is still not available. Install Homebrew (https://brew.sh) then: brew install git")
            elif sys_name == "Windows":
                log_unique("❗ Git is still not available. Install via winget/choco or download from https://git-scm.com/download/win")
            else:
                log_unique("❗ Git is still not available. Install via apt/dnf/yum/pacman or https://git-scm.com/download/linux")
            return None

        # Determine if there was no repo before initialization and collect a project name
        no_repo_before = not _has_git_repo(cwd)
        default_name = _sanitize_repo_name(os.getenv("AWFL_PROJECT_NAME", "") or cwd.name)
        project_name = default_name
        if no_repo_before:
            # Prompt for name; fall back to default on blank
            project_name = _sanitize_repo_name(_prompt_project_name(default_name))

        # Ensure local repository exists and has at least one commit
        _ensure_local_repo_initialized(cwd)

        # If this was a brand-new repo, create an AWFL project without a remote and write config
        if no_repo_before:
            try:
                proj = _create_project_no_remote_sync(project_name)
                if isinstance(proj, dict):
                    pid = (proj.get("id") or proj.get("projectId") or "").strip()
                    if pid:
                        _write_repo_awfl_config(cwd, pid, project_name)
                        set_project_id(pid)
                    else:
                        log_unique("⚠️ Project create returned no id; skipping config init")
                else:
                    log_unique("⚠️ Project create failed; skipping config init")
            except Exception as e:
                log_unique(f"⚠️ Error during project bootstrap: {e}")

        # If a remote already exists, nothing else to do
        if _has_remote(cwd):
            return None

        # Enforce n00b local mode: if git was missing on entry, skip GitHub push this run
        if not had_git_on_entry:
            log_unique(
                "📦 Local git repository initialized (branch 'main' with an initial commit).\n"
                "ℹ️ Skipping GitHub setup for this first run. Later you can run:\n"
                "    gh auth login\n"
                f"    gh repo create {project_name} --source=. --private --remote=origin --push\n"
                "  (or create a repo on GitHub and: git remote add origin <url> && git push -u origin main)"
            )
            return None

        # Try to create/push via GitHub CLI (private by default); otherwise log manual steps
        _attempt_github_push(cwd, repo_name=project_name, visibility=os.getenv("AWFL_GITHUB_VISIBILITY", "private").lower())

    except Exception:
        # Never crash the CLI due to onboarding issues
        return None

    return None
