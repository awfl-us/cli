import time
import base64
import json
from typing import Dict

from awfl.utils import log_unique
from awfl.auth import get_auth_headers, login_google_device, logout_google_device


def _decode_jwt_no_verify(token: str) -> Dict:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        missing = len(payload_b64) % 4
        if missing:
            payload_b64 += '=' * (4 - missing)
        payload_json = base64.urlsafe_b64decode(payload_b64.encode('utf-8')).decode('utf-8')
        return json.loads(payload_json)
    except Exception:
        return {}


def handle_login() -> bool:
    try:
        acct = login_google_device()
        email = acct.get('email') or '(no email)'
        uid = acct.get('firebaseUid')
        log_unique(f"✅ Logged in via Google. Firebase user: {email} (uid={uid})")
        return True
    except Exception as e:
        log_unique(f"❌ Login failed: {e}")
        return True


def print_whoami() -> None:
    try:
        headers = get_auth_headers()
    except Exception as e:
        log_unique(f"🔒 Auth not initialized: {e}")
        return

    if headers.get('X-Skip-Auth') == '1':
        log_unique("🔓 Auth is skipped (SKIP_AUTH=1). Requests will include X-Skip-Auth: 1.")
        return

    authz = headers.get('Authorization')
    if not authz or not authz.startswith('Bearer '):
        log_unique("🔒 No Authorization header is available.")
        return

    token = authz.split(' ', 1)[1]
    payload = _decode_jwt_no_verify(token)
    uid = payload.get('user_id') or payload.get('sub') or 'unknown'
    email = payload.get('email') or 'unknown'
    exp = payload.get('exp')
    if exp:
        ttl = int(exp) - int(time.time())
        ttl_str = f"expires in {ttl}s" if ttl > 0 else f"expired {-ttl}s ago"
    else:
        ttl_str = "no exp"

    log_unique(f"👤 Authenticated as: {email} (uid={uid}, {ttl_str})")


def handle_logout() -> bool:
    return logout_google_device()
