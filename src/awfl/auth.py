import os
import time
import json
import base64
import pathlib
from typing import Dict, Any, Optional, Tuple

import requests

CACHE_DIR = pathlib.Path.home() / ".awfl"
CACHE_PATH = CACHE_DIR / "tokens.json"

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY") or "AIzaSyBPVdMuYlC5dW-yBquEgrNYs5CUYrOJQJ4"
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "323709301334-u7pmm22o8bd95s1ovn6a1u1srfo5qa89.apps.googleusercontent.com"
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "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"

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FIREBASE_IDP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp"
FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

SCOPES = "openid email profile"

# This is the AWFL project id (server-side), not the GCP project
_project_id: Optional[str] = None

def get_project_id():
    return _project_id

def set_project_id(project_id):
    global _project_id
    _project_id = project_id

def _now() -> int:
    return int(time.time())


def _resolve_gcp_project() -> str:
    """Resolve the active GCP project for token scoping.
    Source of truth: per-repo dev config (see awfl/cmds/dev/dev_config.py).
    - Reads ~/.awfl/{repo_name()}/dev_config.json and uses key "project".
    - Falls back to default "awfl-us" if missing.
    """
    try:
        # Local import to avoid circular import at module import time
        from awfl.cmds.dev.dev_config import load_dev_config
        repo_cfg = load_dev_config() or {}
        repo_proj = repo_cfg.get("project")
        if isinstance(repo_proj, str) and repo_proj.strip():
            return repo_proj.strip()
    except Exception:
        # If dev config cannot be loaded yet (e.g., project id not resolved), ignore
        pass
    return "awfl-us"


def _load_cache() -> Dict[str, Any]:
    try:
        if CACHE_PATH.exists():
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
        else:
            d = {}
    except Exception:
        d = {}
    # Normalize structure for backward compatibility
    if not isinstance(d, dict):
        d = {}
    d.setdefault("accounts", {})            # legacy (global)
    d.setdefault("activeUserKey", None)     # legacy (global)
    d.setdefault("byProject", {})           # new: per GCP project buckets
    return d


def _save_cache(cache: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    tmp.replace(CACHE_PATH)


def _project_bucket(cache: Dict[str, Any], gcp_project: str) -> Dict[str, Any]:
    bp = cache.setdefault("byProject", {})
    bucket = bp.get(gcp_project)
    if not isinstance(bucket, dict):
        bucket = {"accounts": {}, "activeUserKey": None}
        bp[gcp_project] = bucket
    bucket.setdefault("accounts", {})
    bucket.setdefault("activeUserKey", None)
    return bucket


def _pick_account_key(email: Optional[str], local_id: str) -> str:
    # Prefer email for readability; fall back to Firebase localId
    return f"google:{email}" if email else f"google:{local_id}"


def _firebase_refresh(refresh_token: str) -> Tuple[str, str, int]:
    if not FIREBASE_API_KEY:
        raise RuntimeError("FIREBASE_API_KEY not set; cannot refresh Firebase token.")
    r = requests.post(
        f"{FIREBASE_REFRESH_URL}?key={FIREBASE_API_KEY}",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()
    id_token = d["id_token"]
    new_refresh = d.get("refresh_token", refresh_token)
    expires_at = _now() + int(d.get("expires_in", 3600)) - 60
    return id_token, new_refresh, expires_at


def _firebase_sign_in_with_google_id_token(google_id_token: str) -> Dict[str, Any]:
    if not FIREBASE_API_KEY:
        raise RuntimeError("FIREBASE_API_KEY not set; cannot exchange Google ID token with Firebase.")
    payload = {
        "postBody": f"id_token={google_id_token}&providerId=google.com",
        "requestUri": "http://localhost",
        "returnSecureToken": True,
        "returnIdpCredential": True,
    }
    r = requests.post(
        f"{FIREBASE_IDP_URL}?key={FIREBASE_API_KEY}",
        json=payload,
        timeout=30,
    )
    if not r.ok:
      print("🚨 Firebase sign-in failed:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


def _google_device_flow() -> str:
    if not GOOGLE_OAUTH_CLIENT_ID:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID not set; cannot start Google Device Flow.")

    if os.getenv("AWFL_DEBUG") == "1":
        print(f"[auth] Using GOOGLE_OAUTH_CLIENT_ID={GOOGLE_OAUTH_CLIENT_ID}")

    # Step 1: request device/user codes
    r = requests.post(
        DEVICE_CODE_URL,
        data={
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "scope": SCOPES,
        },
        timeout=20,
    )
    if r.status_code != 200:
        # Provide detailed diagnostics for setup issues (invalid_client, unauthorized_client, etc.)
        try:
            err = r.json()
        except Exception:
            err = None
        if err:
            e = err.get("error")
            ed = err.get("error_description") or err.get("error_description")
            msg = f"Device code request failed: {e or 'HTTP ' + str(r.status_code)}."
            if ed:
                msg += f" {ed}"
            msg += "\nChecks: ensure the OAuth client type is 'TVs and Limited Input devices' in the SAME GCP project as your consent screen, the consent screen is configured (and your account is a Test user if in Testing), and that you're exporting GOOGLE_OAUTH_CLIENT_ID in this shell."
            raise RuntimeError(msg)
        raise RuntimeError(f"Device code request failed: HTTP {r.status_code} - {r.text}")

    d = r.json()
    device_code = d["device_code"]
    user_code = d["user_code"]
    verification_url = d.get("verification_url") or d.get("verification_uri")
    interval = int(d.get("interval", 5))
    expires_in = int(d.get("expires_in", 1800))

    print("\nTo authenticate, open this URL and enter the code:")
    print(f"  {verification_url}")
    print(f"Code: {user_code}\n")

    # Step 2: poll token endpoint until user completes
    start = _now()
    while _now() - start < expires_in:
        data = {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        # Some Google OAuth clients require a client_secret; include if present
        if GOOGLE_OAUTH_CLIENT_SECRET:
            data["client_secret"] = GOOGLE_OAUTH_CLIENT_SECRET
        t = requests.post(TOKEN_URL, data=data, timeout=20)
        if t.status_code == 200:
            tok = t.json()
            if "id_token" in tok:
                return tok["id_token"]
            # In rare cases access_token only may be returned; we require id_token for Firebase
        else:
            err = None
            try:
                err = t.json()
            except Exception:
                err = None
            if err:
                e = err.get("error")
                ed = err.get("error_description")
                if e in ("authorization_pending", "slow_down"):
                    time.sleep(interval)
                    continue
                if e == "access_denied":
                    raise RuntimeError("Google authorization was denied.")
                if e in ("expired_token", "invalid_grant"):
                    raise RuntimeError(f"Device code expired/invalid ({e}). Please restart login.")
                if e in ("invalid_client", "unauthorized_client", "unsupported_grant_type"):
                    msg = f"OAuth client misconfigured for Device Flow: {e}."
                    if ed:
                        msg += f" {ed}"
                    raise RuntimeError(msg)
                # Fallback to verbose error
                raise RuntimeError(f"Token exchange failed: {e}. {ed or t.text}")
            # No JSON error body; include response text
            raise RuntimeError(f"Token exchange failed: HTTP {t.status_code} - {t.text}")
        time.sleep(interval)

    raise TimeoutError("Google Device authorization timed out. Please try again.")


def login_google_device(gcp_project: Optional[str] = None) -> Dict[str, Any]:
    """Run Google Device Flow and sign into Firebase. Returns the stored account record for the project."""
    project = gcp_project or _resolve_gcp_project()
    google_id_token = _google_device_flow()
    fb = _firebase_sign_in_with_google_id_token(google_id_token)

    id_token = fb["idToken"]
    refresh_token = fb["refreshToken"]
    expires_at = _now() + int(fb.get("expiresIn", 3600)) - 60
    local_id = fb["localId"]
    email = fb.get("email")

    cache = _load_cache()
    bucket = _project_bucket(cache, project)
    key = _pick_account_key(email, local_id)
    bucket["accounts"][key] = {
        "provider": "google",
        "firebaseUid": local_id,
        "email": email,
        "idToken": id_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
    }
    bucket["activeUserKey"] = key

    # Do not clobber legacy root unless it's empty; keep backward compatibility for readers
    # if not cache.get("accounts") and not cache.get("activeUserKey"):
    #     cache["accounts"][key] = bucket["accounts"][key]
    #     cache["activeUserKey"] = key

    _save_cache(cache)
    return bucket["accounts"][key]


def _get_active_account_for_project(cache: Dict[str, Any], gcp_project: str) -> Optional[Dict[str, Any]]:
    bucket = _project_bucket(cache, gcp_project)
    key = bucket.get("activeUserKey")
    if key and key in bucket.get("accounts", {}):
        return bucket["accounts"][key]
    # Fallback to legacy root; if found, migrate into project bucket
    # legacy_key = cache.get("activeUserKey")
    # if legacy_key and legacy_key in cache.get("accounts", {}):
    #     acct = cache["accounts"][legacy_key]
    #     bucket["accounts"][legacy_key] = acct
    #     bucket["activeUserKey"] = legacy_key
    #     _save_cache(cache)
    #     return acct
    return None


def ensure_active_account(gcp_project: Optional[str] = None, prompt_login: bool = False) -> Dict[str, Any]:
    project = gcp_project or _resolve_gcp_project()
    cache = _load_cache()
    acct = _get_active_account_for_project(cache, project)
    if acct:
        return acct
    # No active account for this project; run login and store in that bucket
    if prompt_login:
      return login_google_device(project)
    else:
        raise Exception("🚫 Must authenticate in main process")


def _refresh_if_needed(acct: Dict[str, Any], gcp_project: Optional[str] = None) -> Dict[str, Any]:
    if _now() < int(acct.get("expiresAt", 0)):
        return acct
    id_token, refresh_token, expires_at = _firebase_refresh(acct.get("refreshToken"))
    acct.update({
        "idToken": id_token,
        "RefreshToken": refresh_token or acct.get("refreshToken"),
        "expiresAt": expires_at,
    })
    # Persist update within the appropriate project bucket if possible
    project = gcp_project or _resolve_gcp_project()
    cache = _load_cache()
    bucket = _project_bucket(cache, project)
    # Try to locate the account by firebaseUid within the project bucket
    target_key: Optional[str] = None
    for k, v in bucket.get("accounts", {}).items():
        if v.get("firebaseUid") == acct.get("firebaseUid"):
            target_key = k
            break
    # If not found in bucket, also check legacy root and migrate
    if not target_key:
        for k, v in cache.get("accounts", {}).items():
            if v.get("firebaseUid") == acct.get("firebaseUid"):
                target_key = k
                bucket["accounts"][k] = acct
                bucket["activeUserKey"] = k
                break
    if target_key:
        bucket["accounts"][target_key] = acct
        if bucket.get("activeUserKey") is None:
            bucket["activeUserKey"] = target_key
        _save_cache(cache)
    return acct


def logout_google_device(gcp_project: Optional[str] = None) -> bool:
    """
    Log out from Firebase/Google for the given GCP project.
    Removes the stored account and activeUserKey for that project only.
    Returns True if a token was removed, False if none existed.
    """
    project = gcp_project or _resolve_gcp_project()
    cache = _load_cache()
    bp = cache.get("byProject", {})

    if project not in bp:
        print(f"⚠️ No cached tokens found for project '{project}'.")
        return False

    # Remove the project's auth bucket
    removed = bp.pop(project, None)
    _save_cache(cache)

    if removed and removed.get("accounts"):
        print(f"🧹 Logged out of {project}: removed {len(removed['accounts'])} cached account(s).")
        return True

    print(f"⚠️ No active accounts to remove for project '{project}'.")
    return False


def get_auth_headers() -> Dict[str, str]:
    """
    Resolve auth headers for API calls.
    - If SKIP_AUTH=1: return { 'X-Skip-Auth': '1' }
    - If FIREBASE_ID_TOKEN is set: use it
    - Else ensure a Firebase user session via Google Device Flow and refresh as needed
    Scopes token storage by the per-repo dev config GCP project, defaulting to 'awfl-us'.
    """
    headers: Dict[str, str] = {}

    if _project_id:
        headers["x-project-id"] = _project_id

    override = os.getenv("FIREBASE_ID_TOKEN")

    if os.getenv("SKIP_AUTH") == "1":
        headers["X-Skip-Auth"] = "1"

    elif override:
        headers["Authorization"] = f"Bearer {override}"

    else:
        gcp_project = _resolve_gcp_project()
        acct = ensure_active_account(gcp_project)
        acct = _refresh_if_needed(acct, gcp_project)
        headers["Authorization"] = f"Bearer {acct['idToken']}"

    return headers
