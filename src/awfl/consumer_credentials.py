"""Refresh short-lived Google Cloud credentials for an AWFL project consumer.

The OAuth refresh token stays on the AWFL server. This process only receives
short-lived access tokens over a lock-bound capability endpoint.
"""
import json
import os
import pathlib
import random
import sys
import tempfile
import time
import urllib.error
import urllib.request

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
    token = result["accessToken"]
    if not isinstance(token, str) or not token:
        raise ValueError("Credential refresh returned no access token")
    target = pathlib.Path(os.environ["CLOUDSDK_AUTH_ACCESS_TOKEN_FILE"])
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".token-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(token + "\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def main():
    once = "--once" in sys.argv
    while True:
        try:
            refresh()
            if once:
                return
            time.sleep(2400 + random.randrange(120))
        except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
            print("[awfl] Google credential refresh failed:", type(exc).__name__, file=sys.stderr, flush=True)
            if once:
                raise SystemExit(1)
            time.sleep(60)

if __name__ == "__main__":
    main()
