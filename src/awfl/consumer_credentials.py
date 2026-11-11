"""Refresh short-lived Google Cloud and GitHub credentials for an AWFL project consumer.

The OAuth refresh token stays on the AWFL server. This process only receives
short-lived access tokens over a lock-bound capability endpoint.
"""
import json
import math
import os
import pathlib
import random
import sys
import tempfile
import time
import urllib.error
import urllib.request

# A Google OAuth token typically lasts 3600 seconds, but the server may give
# us a cached token that has already consumed part of that lifetime.
MAX_REFRESH_SECONDS = 35 * 60
EXPIRY_MARGIN_SECONDS = 5 * 60
MIN_REFRESH_SECONDS = 15
RETRY_SECONDS = 20


def next_refresh_delay(expires_in, github_expires_in=None, github_present=False):
    """Schedule before the earliest credential expiry, including cached tokens."""
    def delay(ttl):
        if ttl is None:
            return MAX_REFRESH_SECONDS
        try:
            remaining = float(ttl)
        except (TypeError, ValueError, OverflowError):
            return MIN_REFRESH_SECONDS
        if not math.isfinite(remaining):
            return MIN_REFRESH_SECONDS
        return max(MIN_REFRESH_SECONDS,
                   min(MAX_REFRESH_SECONDS, int(remaining - EXPIRY_MARGIN_SECONDS)))

    google_delay = delay(expires_in)
    if not github_present:
        return google_delay
    # An older broker may send GitHub tokens without their expiration.
    # Refresh conservatively until both sides support explicit GitHub TTLs.
    github_delay = delay(github_expires_in) if github_expires_in is not None else 5 * 60
    return min(google_delay, github_delay)


def _atomic_write(path, token):
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".credential-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(token + "\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git_credential():
    """Git's credential protocol: supply the latest token without printing it to logs."""
    fields = dict(line.strip().split("=", 1) for line in sys.stdin if "=" in line)
    if fields.get("protocol") != "https" or fields.get("host") != "github.com":
        return
    path = os.environ.get("AWFL_GITHUB_TOKEN_FILE")
    if not path:
        return
    try:
        token = pathlib.Path(path).read_text().strip()
    except OSError:
        return
    if token:
        sys.stdout.write("username=x-access-token\npassword=" + token + "\n\n")


def refresh():
    origin = os.environ["AWFL_CREDENTIAL_REFRESH_ORIGIN"].rstrip("/")
    payload = {key: os.environ[env] for key, env in (
        ("userId", "AWFL_USER_ID"),
        ("projectId", "AWFL_PROJECT_ID"),
        ("consumerId", "AWFL_CONSUMER_ID"),
    )}
    request = urllib.request.Request(
        origin + "/consumer-credentials/refresh",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-awfl-consumer-credential": os.environ["AWFL_CREDENTIAL_REFRESH_CAPABILITY"],
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    google_token = result.get("accessToken")
    github_token = result.get("githubToken")
    if not google_token and not github_token:
        raise ValueError("Credential refresh returned no usable credentials")
    google_ttl = result.get("expiresIn") if google_token else None
    github_ttl = result.get("githubExpiresIn") if github_token else None
    if google_token and os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN_FILE"):
        _atomic_write(os.environ["CLOUDSDK_AUTH_ACCESS_TOKEN_FILE"], google_token)
    if github_token and os.environ.get("AWFL_GITHUB_TOKEN_FILE"):
        _atomic_write(os.environ["AWFL_GITHUB_TOKEN_FILE"], github_token)
    return next_refresh_delay(google_ttl, github_ttl, github_present=bool(github_token))

def main():
    if "--git-credential" in sys.argv:
        git_credential()
        return
    once = "--once" in sys.argv
    while True:
        try:
            delay = refresh()
            if once:
                return
            # Jitter earlier, never later than the expiry-based deadline.
            time.sleep(max(MIN_REFRESH_SECONDS, delay - random.randrange(30)))
        except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
            print("[awfl] Consumer credential refresh failed:", type(exc).__name__, file=sys.stderr, flush=True)
            if once:
                raise SystemExit(1)
            time.sleep(RETRY_SECONDS)

if __name__ == "__main__":
    main()


def subprocess_credentials(base=None):
    """Refresh GitHub credentials for each spawned tool process.

    A background refresher cannot mutate its already-running parent's
    environment, so tools must read the rotated token file on each spawn.
    Git's credential helper also uses this same file.
    """
    env = dict(base if base is not None else os.environ)
    path = env.get("AWFL_GITHUB_TOKEN_FILE")
    if path:
        try:
            token = pathlib.Path(path).read_text().strip()
        except (OSError, UnicodeError):
            token = ""
        if token:
            env["GITHUB_TOKEN"] = token
            env["GH_TOKEN"] = token
    return env
