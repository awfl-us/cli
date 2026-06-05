"""
Git onboarding module extracted from main.py.

Public API:
- first_run_git_onboarding(): Perform interactive first-run Git/GitHub onboarding.

Implementation note: This is a temporary stub so main.py can import it while the
section is being fully implemented by the dedicated crawler. The crawler will
replace this with the full implementation based on the commit patch.
"""

from __future__ import annotations

def first_run_git_onboarding() -> None:
    """Temporary no-op placeholder. To be implemented by crawler.
    The real implementation should perform:
    - Best-effort ensure git is installed (optionally attempt installation).
    - Initialize a local repo and initial commit if missing.
    - If no remote is configured, prompt for a repo name and attempt to create/push
      using GitHub CLI (gh) when available and authenticated.
    - Never crash the CLI; log best-effort status.
    - Skip completely in headless mode.
    """
    pass
