import os
from pathlib import Path
from typing import Dict, List, Optional

import requests

from awfl.state import set_active_workflow, get_active_workflow, normalize_workflow, get_workflow_env_suffix
from awfl.utils import log_unique, get_api_origin, LOCATION
from awfl.auth import get_auth_headers
from .common import get_orig_cwd


# def resolve_workflows_dir() -> str:
#     """Strictly resolve to "$AWFL_ORIG_CWD/workflows/yaml_gens" (no walk-up)."""
#     orig = get_orig_cwd()
#     return str(Path(orig) / "workflows" / "yaml_gens")


def collect_workflow_names(workflows_dir: str) -> List[str]:
    names: List[str] = []
    if not os.path.isdir(workflows_dir):
        return names
    for _root, _dirs, files in os.walk(workflows_dir):
        for f in files:
            if f.endswith(".yaml") or f.endswith(".yml"):
                base = os.path.splitext(f)[0]
                names.append(base)
    names.sort()
    return names


def _get_env_suffix() -> str:
    # Prefer process-local state over environment; falls back to env if needed
    suffix = get_workflow_env_suffix()
    if suffix is None:
        suffix = os.getenv("WORKFLOW_ENV", "")
    return suffix or ""


def _strip_env_suffix(name: str, suffix: str) -> str:
    if name and suffix and name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def build_tree(names: List[str]) -> Dict[str, dict]:
    tree: Dict[str, dict] = {}
    for name in names:
        # Parse names split by '-' (as requested)
        parts = [p for p in name.split("-") if p]
        if not parts:
            continue
        node = tree
        for i, part in enumerate(parts):
            if part not in node:
                node[part] = {"_children": {}, "_full": None}
            if i == len(parts) - 1:
                node[part]["_full"] = name
            # descend into the children dict for the current part
            node = node[part]["_children"]
    return tree


def navigate_tree(tree: Dict[str, dict]) -> None:
    stack: List[tuple] = []  # (label, node)
    current = {"_children": tree, "_full": None}

    while True:
        children_keys = sorted(current["_children"].keys())
        path_display = "/".join([label for (label, _node) in stack]) or "Workflows"
        print(f"\n=== {path_display} ===")
        active = get_active_workflow() or "codebase-ProjectManager"
        print(f"Active workflow: {active}")

        # If this node is a pure leaf (no children), select it immediately
        if not children_keys and current.get("_full"):
            full = current["_full"]
            wf = normalize_workflow(full)
            set_active_workflow(wf)
            log_unique(f"🔀 Active assistant workflow set to: {wf}")
            log_unique(f"To call explicitly: call {wf}")
            return

        # Build selectable options: allow selecting the current node (if it maps to a workflow)
        # and for each child, optionally select the child workflow and/or enter its subgroup.
        options: List[dict] = []  # each: {type: 'select'|'enter', label: str, key/full: str}

        if current.get("_full"):
            options.append({
                "type": "select",
                "label": f"[select] {current['_full']}",
                "full": current["_full"],
            })

        for key in children_keys:
            child = current["_children"][key]
            if child.get("_full"):
                options.append({
                    "type": "select",
                    "label": f"{key} [select]",
                    "full": child["_full"],
                })
            if child["_children"]:
                options.append({
                    "type": "enter",
                    "label": f"{key}/",
                    "key": key,
                })

        if not options:
            log_unique("No workflows to display in this branch.")

        for idx, opt in enumerate(options, start=1):
            print(f"{idx}. {opt['label']}")

        if stack:
            print("b. Back")
        print("q. Quit menu")

        choice = input("Select an option: ").strip()
        if choice.lower() in ("q", "quit", "x", "exit"):
            log_unique("Exited workflow menu.")
            return
        if choice.lower() in ("b", "back"):
            if stack:
                _label, parent = stack.pop()
                current = parent
            else:
                return
            continue

        try:
            idx = int(choice)
            if idx < 1 or idx > len(options):
                raise ValueError
        except ValueError:
            log_unique("Invalid selection. Choose a listed number, 'b' to go back, or 'q' to quit.")
            continue

        opt = options[idx - 1]
        if opt["type"] == "select":
            wf = normalize_workflow(opt["full"])  # normalize to dash form
            set_active_workflow(wf)
            log_unique(f"🔀 Active assistant workflow set to: {wf}")
            log_unique(f"To call explicitly: call {wf}")
            return
        elif opt["type"] == "enter":
            key = opt["key"]
            next_node = current["_children"][key]
            stack.append((key, current))
            current = next_node


def _fetch_remote_workflow_names(location: Optional[str]) -> Optional[List[str]]:
    """Fetch workflow names from the API. Returns a list of unsuffixed IDs, or None on failure."""
    origin = get_api_origin()
    loc = (
        location
        or os.getenv("WORKFLOWS_LOCATION")
        or os.getenv("AWFL_GCLOUD_LOCATION")
        or LOCATION
        or "us-central1"
    )
    url = f"{origin}/workflows/list?location={loc}"

    # Log intent (source and params)
    log_unique(f"📡 Fetching workflows from API: {origin} (location={loc})")

    try:
        headers = get_auth_headers()
    except Exception as e:
        log_unique(f"⚠️ Auth headers unavailable, API list may fail: {e}")
        headers = {}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except Exception as e:
        log_unique(f"⚠️ API request error: {e}")
        return None

    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        log_unique(f"⚠️ API list failed ({resp.status_code}): {body}")
        return None

    try:
        payload = resp.json()
        workflows = payload.get("workflows") or []
    except Exception as e:
        log_unique(f"⚠️ Could not parse API response JSON: {e}")
        return None

    suffix = _get_env_suffix()
    names: List[str] = []
    for wf in workflows:
        wf_id = (wf.get("id") or "").strip()
        if not wf_id:
            continue
        # Strip env suffix if present so selection stays portable across exec modes
        wf_id = _strip_env_suffix(wf_id, suffix)
        names.append(wf_id)

    # Deduplicate and sort
    names = sorted(sorted(set(names)))

    count = len(names)
    project = payload.get("projectId") or os.getenv("GCP_PROJECT") or os.getenv("GCLOUD_PROJECT") or "(unknown)"
    effective_loc = payload.get("location") or loc
    log_unique(f"✅ Retrieved {count} workflow(s) from {project}/{effective_loc}")

    return names


def ls_workflows_interactive() -> None:
    # Try remote API first
    names = _fetch_remote_workflow_names(location=None)

    # if not names:
    #     # Graceful fallback to local directory listing
    #     workflows_dir = resolve_workflows_dir()
    #     names = collect_workflow_names(workflows_dir)
    #     if not names:
    #         log_unique(f"No workflows found under {workflows_dir}.")
    #         return
    #     p = Path(workflows_dir)
    #     if p.name == "yaml_gens" and p.parent.name == "workflows":
    #         display_dir = "workflows/yaml_gens"
    #     else:
    #         orig = get_orig_cwd()
    #         try:
    #             display_dir = os.path.relpath(workflows_dir, start=orig)
    #         except Exception:
    #             display_dir = workflows_dir
    #     log_unique(f"📁 Listing workflows from local disk: {display_dir}")

    tree = build_tree(names)
    navigate_tree(tree)
