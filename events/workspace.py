import asyncio
import json
import os
import re
import subprocess
from typing import Optional, Tuple, Dict, Any

import aiohttp

from auth import get_auth_headers, set_project_id
from utils import get_api_origin, log_unique

# Local cache to avoid race/consistency issues when coordinating multiple consumers
_PROJECT_CACHE_PATH = os.path.expanduser("~/.awfl/projects_by_remote.json")

def _normalize_remote(remote: str) -> str:
    if not remote:
        return ""
    r = remote.strip()
    prefixes = [
        "git@",
        "https://",
        "http://",
        "ssh://",
        "git://",
    ]
    for p in prefixes:
        if r.startswith(p):
            r = r[len(p):]
            break
    # Important: keep suffixes (like .git) as-is per service contract
    return r


def _detect_git_root() -> Optional[str]:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        )
        root = (res.stdout or "").strip()
        return root or None
    except Exception:
        return None


def _get_git_remote(root: Optional[str]) -> Optional[str]:
    try:
        cmd = ["git"]
        if root:
            cmd += ["-C", root]
        cmd += ["remote", "get-url", "origin"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        remote = (res.stdout or "").strip()
        return remote or None
    except Exception:
        return None


def _derive_project_name(remote_normalized: str) -> Optional[str]:
    """Return a human name like 'org/repo' from a normalized remote.
    Examples:
      github.com/org/repo.git -> org/repo
      github.com:org/repo.git -> org/repo
    """
    if not remote_normalized:
        return None
    r = remote_normalized
    # Split after host delimiter ('/' or ':')
    if '/' in r:
        after = r.split('/', 1)[1]
    elif ':' in r:
        after = r.split(':', 1)[1]
    else:
        after = r
    # Strip trailing .git (human-friendly name only)
    after = after[:-4] if after.endswith('.git') else after
    # Expect org/repo
    parts = after.split('/')
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return after or None


def _load_project_cache() -> Dict[str, Any]:
    try:
        with open(_PROJECT_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_project_cache(obj: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_PROJECT_CACHE_PATH), exist_ok=True)
        tmp = _PROJECT_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, _PROJECT_CACHE_PATH)
    except Exception:
        pass


async def fetch_projects(session: aiohttp.ClientSession) -> list[dict]:
    origin = get_api_origin()
    url = f"{origin}/api/workflows/projects"
    headers = {}
    try:
        headers.update(get_auth_headers())
    except Exception:
        pass
    try:
        async with session.get(url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                text = await resp.text()
                log_unique(f"⚠️ Failed to list projects ({resp.status}): {text[:300]}")
                return []
            data = await resp.json(content_type=None)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                arr = data.get("projects")
                return arr if isinstance(arr, list) else []
            return []
    except Exception as e:
        log_unique(f"⚠️ Error fetching projects: {e}")
        return []


async def create_project(
    session: aiohttp.ClientSession,
    remote: str,
    name: Optional[str] = None,
    live: Optional[bool] = None,
) -> Optional[dict]:
    """Create a project via POST /workflows/projects.

    The service generates a random id; we only send remote, optional name and live.
    Returns the created project object or None on failure.
    """
    origin = get_api_origin()
    url = f"{origin}/api/workflows/projects"
    headers = {"Content-Type": "application/json"}
    try:
        headers.update(get_auth_headers())
    except Exception:
        pass

    payload: Dict[str, Any] = {"remote": remote}
    if name:
        payload["name"] = name.strip()
    if live is not None:
        payload["live"] = bool(live)

    try:
        async with session.post(url, headers=headers, data=json.dumps(payload), timeout=20) as resp:
            text = await resp.text()
            if resp.status not in (200, 201):
                log_unique(f"⚠️ Project create failed ({resp.status}): {text[:300]}")
                return None
            try:
                data = json.loads(text)
            except Exception:
                data = None
            if isinstance(data, dict):
                proj = data.get("project") if isinstance(data.get("project"), dict) else None
                if proj:
                    log_unique(f"🆕 Created project: {proj.get('id') or ''} ({proj.get('name') or ''})")
                    return proj
            return None
    except Exception as e:
        log_unique(f"⚠️ Error creating project: {e}")
        return None


async def resolve_project_id(
    session: aiohttp.ClientSession,
    *,
    create_if_missing: bool = True,
) -> Optional[str]:
    """Resolve the project id for the current git repo.

    - Checks a local cache (by normalized remote) to avoid races/consistency gaps.
    - Lists existing projects and matches by normalized remote.
    - If create_if_missing is True, creates a new project with name derived from org/repo.
    - Returns the project id or None if not found/created.
    """
    root = _detect_git_root()
    if not root:
        log_unique("ℹ️ Not in a git repo; cannot resolve project for workspace.")
        return None
    remote = _get_git_remote(root)
    if not remote:
        log_unique("ℹ️ No git remote 'origin' found; cannot resolve project for workspace.")
        return None

    norm = _normalize_remote(remote)

    # 1) Local cache first
    cache = _load_project_cache()
    cached_id = cache.get(norm)
    if isinstance(cached_id, str) and cached_id:
        set_project_id(cached_id)
        return cached_id

    # 2) Service list
    projs = await fetch_projects(session)
    for p in projs:
        r = p.get("remote") or ""
        if _normalize_remote(str(r)) == norm:
            pid = p.get("id") or p.get("projectId")
            if pid:
                cache[norm] = pid
                _save_project_cache(cache)
            set_project_id(pid)
            return pid

    if not create_if_missing:
        return None

    # 3) Not found -> create it using org/repo as the display name only
    name = _derive_project_name(norm)
    created = await create_project(session, remote=norm, name=name, live=False)
    if created and isinstance(created, dict):
        pid = created.get("id") or created.get("projectId")
        if pid:
            cache[norm] = pid
            _save_project_cache(cache)
        set_project_id(pid)
        return pid

    log_unique(f"⚠️ No matching project found for remote: {norm}")
    return None


async def resolve_workspace(
    session: aiohttp.ClientSession,
    project_id: str,
    session_id: Optional[str] = None,
    ttl_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    origin = get_api_origin()
    url = f"{origin}/api/workflows/workspace/resolve"
    params: Dict[str, Any] = {"projectId": project_id}
    if session_id:
        params["sessionId"] = session_id
    if ttl_ms is not None:
        params["ttlMs"] = str(ttl_ms)

    headers = {}
    try:
        headers.update(get_auth_headers())
    except Exception:
        pass

    try:
        async with session.get(url, headers=headers, params=params, timeout=20) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                text = await resp.text()
                log_unique(f"⚠️ Workspace resolve failed ({resp.status}): {text[:300]}")
                return None
            data = await resp.json(content_type=None)
            ws = data.get("workspace") if isinstance(data, dict) else None
            return ws if isinstance(ws, dict) else None
    except Exception as e:
        log_unique(f"⚠️ Error resolving workspace: {e}")
        return None


async def register_workspace(
    session: aiohttp.ClientSession,
    project_id: str,
    session_id: Optional[str] = None,
) -> Optional[str]:
    origin = get_api_origin()
    url = f"{origin}/api/workflows/workspace/register"

    headers = {"Content-Type": "application/json"}
    try:
        headers.update(get_auth_headers())
    except Exception:
        pass
    payload: Dict[str, Any] = {"projectId": project_id}
    if session_id:
        payload["sessionId"] = session_id

    try:
        async with session.post(url, headers=headers, data=json.dumps(payload), timeout=20) as resp:
            text = await resp.text()
            if resp.status != 200 and resp.status != 201:
                log_unique(f"⚠️ Workspace register failed ({resp.status}): {text[:300]}")
                return None
            # Accept either { id } or { workspace: { id } }
            try:
                data = json.loads(text)
            except Exception:
                data = None
            ws_id = None
            if isinstance(data, dict):
                ws_id = data.get("id")
                if not ws_id and isinstance(data.get("workspace"), dict):
                    ws_id = data["workspace"].get("id")
            if not ws_id:
                log_unique("⚠️ Workspace register returned no id")
            return ws_id
    except Exception as e:
        log_unique(f"⚠️ Error registering workspace: {e}")
        return None


async def get_or_create_workspace(
    session: aiohttp.ClientSession,
    project_id: str,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve appropriate workspace for project/session, registering if necessary.

    Behavior aligned with RELAY.md:
    - If resolving a session-scoped workspace returns a project-wide workspace (no sessionId), register a new
      session-scoped workspace and return its id.
    - Otherwise, return the resolved workspace id; if none exists, register and return the new id.
    """
    ws = await resolve_workspace(session, project_id, session_id=session_id)
    if ws and isinstance(ws, dict):
        # If a session was requested but we only found a project-wide workspace, register a dedicated session workspace
        if session_id:
            ws_sid = ws.get("sessionId")
            if not ws_sid or str(ws_sid) != str(session_id):
                log_unique(
                    "ℹ️ Resolved project-wide workspace while session-specific was requested; registering a session workspace."
                )
                return await register_workspace(session, project_id, session_id=session_id)
        return ws.get("id")
    # Register new
    return await register_workspace(session, project_id, session_id=session_id)
