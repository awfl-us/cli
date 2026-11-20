"""Interactive-only project setup, before starting SSE consumers."""

from pathlib import Path

from awfl.local_project import init_local_project


def prompt_for_project(input_fn=input, init_fn=init_local_project, cwd: Path | None = None) -> str | None:
    """Offer one explicit setup when no project can be resolved; None means cancelled.

    No Git operations or network writes happen until the user accepts.
    """
    root = cwd or Path.cwd()
    try:
        accepted = input_fn(f'No AWFL project found for {root}. Create one? [Y/n] ').strip().lower()
        if accepted not in ('', 'y', 'yes'):
            return None
        default_name = root.name or 'local-project'
        chosen = input_fn(f'Project name [{default_name}]: ').strip() or default_name
        project_id, path = init_fn(chosen, root)
        print(f'Created AWFL project {project_id}; saved {path}')
        return project_id
    except (EOFError, KeyboardInterrupt):
        return None
