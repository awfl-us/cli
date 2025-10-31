from __future__ import annotations

import os
from pathlib import Path
from typing import List

from utils import log_unique

from ..core import deploy_workflow


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
    location = os.getenv("AWFL_GCLOUD_LOCATION", "us-central1")
    project = os.getenv("PROJECT", "topaigents")
    deploy_workflow(yaml_path, location, project)
    return True


__all__ = ["deploy_workflow_cmd"]
