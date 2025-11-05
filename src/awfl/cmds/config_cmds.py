import os
from awfl.utils import log_unique
from .auth_cmds import print_whoami
# from .workflows import resolve_workflows_dir


def set_exec_mode(mode: str) -> bool:
    mode = mode.strip().lower()
    if mode not in ('api', 'gcloud'):
        log_unique("❌ Invalid mode. Use 'api' or 'gcloud'.")
        return True
    os.environ['WORKFLOW_EXEC_MODE'] = mode
    log_unique(f"🔧 Set WORKFLOW_EXEC_MODE={mode}.")
    return True


def set_api_origin(url: str) -> bool:
    url = url.strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        log_unique("❌ API origin must start with http:// or https://")
        return True
    os.environ['API_ORIGIN'] = url.rstrip('/')
    log_unique(f"🌐 Set API_ORIGIN={os.environ['API_ORIGIN']}")
    return True


def set_skip_auth(on: bool) -> bool:
    if on:
        os.environ['SKIP_AUTH'] = '1'
        log_unique("🔓 Enabled SKIP_AUTH (requests will include X-Skip-Auth: 1)")
    else:
        if 'SKIP_AUTH' in os.environ:
            del os.environ['SKIP_AUTH']
        log_unique("🔒 Disabled SKIP_AUTH")
    return True


def set_token_override(token: str | None) -> bool:
    if token:
        os.environ['FIREBASE_ID_TOKEN'] = token.strip()
        log_unique("🪪 Set FIREBASE_ID_TOKEN override (will be used for Authorization).")
    else:
        if 'FIREBASE_ID_TOKEN' in os.environ:
            del os.environ['FIREBASE_ID_TOKEN']
        log_unique("🪪 Cleared FIREBASE_ID_TOKEN override.")
    return True


def print_status() -> None:
    mode = os.getenv('WORKFLOW_EXEC_MODE', 'api').lower()
    origin = os.getenv('API_ORIGIN') or 'http://localhost:5050'
    skip = os.getenv('SKIP_AUTH') == '1'
    has_override = bool(os.getenv('FIREBASE_ID_TOKEN'))
    # wf_dir = resolve_workflows_dir()
    log_unique(f"⚙️ Exec mode: {mode} | API_ORIGIN: {origin} | SKIP_AUTH={skip} | OVERRIDE_TOKEN={'yes' if has_override else 'no'}")
    if mode == 'api':
        print_whoami()
