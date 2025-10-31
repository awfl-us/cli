import os
import time
import json
import base64
import pathlib
from typing import Dict, Any, Optional, Tuple

import requests

CACHE_DIR = pathlib.Path.home() / ".awfl"
CACHE_PATH = CACHE_DIR / "tokens.json"

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY") or "AIzaSyCuwFP2SA6GPGfdKLl4S4Tt7kvWSdZySw8"
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "85050401291-9lv9mf68b12md4q41rhra1fhgmfikdta.apps.googleusercontent.com"
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "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" # optional, may be unused for public clients

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FIREBASE_IDP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp"
FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

SCOPES = "openid email profile"

_project_id: Optional[str] = None

# def get_project_id():
#     return _project_id

def set_project_id(project_id):
    global _project_id
    _project_id = project_id

def _now() -> int:
    return int(time.time())


def _load_cache() -> Dict[str, Any]:
    try:
        if CACHE_PATH.exists():
            with open(CACHE_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"accounts": {}, "activeUserKey": None}


def _save_cache(cache: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2)
    tmp.replace(CACHE_PATH)


def _pick_account_key(email: Optional[str], local_id: str) -> str:
    # Prefer email for readability; fall back to Firebase localId
    return f"google:{email}" if email else f"google:{local_id}"


def _parse_jwt_no_verify(jwt_token: str) -> Dict[str, Any]:
    try:
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1] + "=="  # pad
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
        return json.loads(payload_json)
    except Exception:
        return {}


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


def login_google_device() -> Dict[str, Any]:
    """Run Google Device Flow and sign into Firebase. Returns the stored account record."""
    google_id_token = _google_device_flow()
    fb = _firebase_sign_in_with_google_id_token(google_id_token)

    id_token = fb["idToken"]
    refresh_token = fb["refreshToken"]
    expires_at = _now() + int(fb.get("expiresIn", 3600)) - 60
    local_id = fb["localId"]
    email = fb.get("email")

    cache = _load_cache()
    key = _pick_account_key(email, local_id)
    cache["accounts"][key] = {
        "provider": "google",
        "firebaseUid": local_id,
        "email": email,
        "idToken": id_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
    }
    cache["activeUserKey"] = key
    _save_cache(cache)
    return cache["accounts"][key]


def ensure_active_account() -> Dict[str, Any]:
    cache = _load_cache()
    key = cache.get("activeUserKey")
    if key and key in cache.get("accounts", {}):
        return cache["accounts"][key]
    # No active account; run login
    return login_google_device()


def _refresh_if_needed(acct: Dict[str, Any]) -> Dict[str, Any]:
    if _now() < int(acct.get("expiresAt", 0)):
        return acct
    id_token, refresh_token, expires_at = _firebase_refresh(acct.get("refreshToken"))
    acct.update({
        "idToken": id_token,
        "refreshToken": refresh_token or acct.get("refreshToken"),
        "expiresAt": expires_at,
    })
    cache = _load_cache()
    key = None
    for k, v in cache.get("accounts", {}).items():
        if v.get("firebaseUid") == acct.get("firebaseUid"):
            key = k
            break
    if key:
        cache["accounts"][key] = acct
        if cache.get("activeUserKey") is None:
            cache["activeUserKey"] = key
        _save_cache(cache)
    return acct


def get_auth_headers() -> Dict[str, str]:
    """
    Resolve auth headers for API calls.
    - If SKIP_AUTH=1: return { 'X-Skip-Auth': '1' }
    - If FIREBASE_ID_TOKEN is set: use it
    - Else ensure a Firebase user session via Google Device Flow and refresh as needed
    """
    headers = {}

    if _project_id:
        headers["x-project-id"] = _project_id

    override = os.getenv("FIREBASE_ID_TOKEN")

    if os.getenv("SKIP_AUTH") == "1":
        headers["X-Skip-Auth"] = "1"

    elif override:
        headers["Authorization"] = f"Bearer {override}"

    else:
      acct = ensure_active_account()
      acct = _refresh_if_needed(acct)
      headers["Authorization"] = f"Bearer {acct['idToken']}"

    return headers
