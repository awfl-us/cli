"""Opt-in local-project binding, independent of Git remotes."""

import json
import os
from pathlib import Path
import tempfile


def config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / '.awfl' / 'config.json'


def read_local_config(cwd: Path | None = None) -> dict | None:
    path = config_path(cwd)
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('.awfl/config.json must be a JSON object')
    return obj


def store_local_project(project_id: str, name: str, cwd: Path | None = None) -> Path:
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError('Server returned no project ID')
    path = config_path(cwd)
    if path.exists():
        raise FileExistsError(f'{path} already exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.config-',
                                         dir=path.parent, delete=False) as fp:
            tmp = Path(fp.name)
            os.chmod(tmp, 0o600)
            json.dump({'projectId': project_id.strip(), 'name': name}, fp, indent=2)
            fp.write('\n')
        os.link(tmp, path)  # exclusive create; never replace an existing binding
        return path
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def init_local_project(name: str, cwd: Path | None = None) -> tuple[str, Path]:
    name = name.strip()
    if not name or len(name) > 128:
        raise ValueError('Project name must contain 1–128 characters')
    if config_path(cwd).exists():
        raise FileExistsError('Local project is already configured')

    import requests
    from awfl.auth import get_auth_headers
    from awfl.utils import get_api_origin
    base = get_api_origin().rstrip('/') + '/workflows/projects'
    headers = get_auth_headers()
    listed = requests.get(base, headers=headers, timeout=20)
    listed.raise_for_status()
    projects = listed.json().get('projects', [])
    if not isinstance(projects, list):
        raise ValueError('Unexpected projects listing')
    if any(isinstance(item, dict) and item.get('name') == name and not item.get('remote')
           for item in projects):
        raise ValueError('A local project with this name already exists; select it explicitly')
    created = requests.post(base, headers=headers, json={'name': name, 'live': False}, timeout=20)
    created.raise_for_status()
    data = created.json().get('project', {})
    if not isinstance(data, dict):
        raise ValueError('Unexpected project creation response')
    project_id = data.get('id') or data.get('projectId')
    return project_id, store_local_project(project_id, name, cwd)
