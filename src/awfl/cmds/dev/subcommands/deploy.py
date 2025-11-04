from __future__ import annotations

from pathlib import Path
from typing import List

from awfl.utils import log_unique

from ..core import deploy_workflow
from ..dev_config import resolve_location_project


def deploy_workflow_cmd(args: List[str]) -> bool:
    if not args:
        log_unique("Usage: dev deploy-workflow workflows/yaml_gens/<file>.yaml")
        return True
    yaml_path = args[0]
    if not Path(yaml_path).exists():
        log_unique(f"⚠️ File not found: {yaml_path}")
        return True
    if "yaml_gens" not in yaml_path:
        log_unique("⚠️ Expected a path under workflows/yaml_gens.")
    location, project = resolve_location_project()
    deploy_workflow(yaml_path, location, project)
    return True


__all__ = ["deploy_workflow_cmd"]
