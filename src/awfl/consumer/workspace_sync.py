"""Project-scoped, generation-safe GCS persistence for the existing CLOUD consumer.

The server is the token broker. This module never uses the user's Google
OAuth token or grants the GKE service account bucket-wide permissions.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from urllib.parse import quote

import aiohttp


MANIFEST = ".awfl-gcs-manifest.json"
SKIP_DIRS = {
    "node_modules", ".terraform", ".venv", "venv", "__pycache__", ".cache",
    ".ssh", ".aws", ".config", ".local", ".next", "dist", "build",
    "target", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".playwright",
}
SKIP_FILES = {
    MANIFEST, ".env", ".env.local", ".npmrc", ".netrc",
    "serviceAccountKey.json", "credentials.json", "tokens.json",
    "id_rsa", "id_ed25519", "known_hosts", ".git-credentials",
}
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000
STARTUP_RESTORE_CONCURRENCY = 16


def allowed_file(rel: str) -> bool:
    """Never persist credentials, build outputs, or runtime caches."""
    path = PurePosixPath(rel)
    if (not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel or
            any(p in ("", ".", "..") for p in rel.split("/"))):
        return False
    if any(part in SKIP_DIRS for part in path.parts[:-1]):
        return False
    name = path.name
    if name in SKIP_FILES or name.startswith(".env."):
        return False
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".tfstate", ".tfstate.backup", ".tfplan")):
        return False
    if ".git" in path.parts and (name in {"index.lock", "config.lock", "FETCH_HEAD"} or
                                "logs" in path.parts or "hooks" in path.parts):
        return False
    return True


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fingerprint(path: Path, previous: dict | None = None) -> dict | None:
    if path.is_symlink() or not path.is_file():
        return None
    stat = path.stat()
    if stat.st_size > MAX_FILE_BYTES:
        return None
    if previous and previous.get("size") == stat.st_size and previous.get("mtime_ns") == stat.st_mtime_ns:
        return {"sha256": previous["sha256"], "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns, "mode": stat.st_mode & 0o777}
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            value.update(chunk)
    return {"sha256": value.hexdigest(), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "mode": stat.st_mode & 0o777}


def _safe_target(root: Path, rel: str) -> Path:
    if not allowed_file(rel):
        raise ValueError("Unsafe persisted path")
    path = root.joinpath(*PurePosixPath(rel).parts)
    parent = root
    for part in PurePosixPath(rel).parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ValueError("Symlink in persisted path")
    if path.is_symlink():
        raise ValueError("Symlink at persisted path")
    return path


def _atomic_write(path: Path, content: bytes, mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing to replace symlink")
    fd, tmp = tempfile.mkstemp(prefix=".awfl-sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode & 0o777)
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class WorkspaceSync:
    def __init__(self, root: Path, *, origin: str, user_id: str, project_id: str,
                 consumer_id: str, capability: str, interval: float = 30.0):
        self.root = root.resolve()
        self.origin = origin.rstrip("/")
        self.identity = {"userId": user_id, "projectId": project_id, "consumerId": consumer_id}
        self.capability = capability
        self.interval = max(5, interval)
        self._access = None
        self._access_until = 0.0
        self.bucket = ""
        self.prefix = ""
        self.manifest = self._read_manifest()
        self.lock = asyncio.Lock()
        self.task = None

    @classmethod
    def from_environment(cls):
        if os.getenv("AWFL_CONSUMER_TYPE") != "CLOUD" or not os.getenv("AWFL_WORKSPACE_GCS_BUCKET"):
            return None
        names = ("AWFL_CREDENTIAL_REFRESH_ORIGIN", "AWFL_USER_ID", "AWFL_PROJECT_ID",
                 "AWFL_CONSUMER_ID", "AWFL_CREDENTIAL_REFRESH_CAPABILITY")
        if not all(os.getenv(name) for name in names):
            raise RuntimeError("Cloud workspace persistence needs the lock-bound credential broker")
        root = Path(os.getenv("AWFL_CONSUMER_WORKSPACE") or os.getcwd()).resolve()
        return cls(root, origin=os.environ[names[0]], user_id=os.environ[names[1]],
                   project_id=os.environ[names[2]], consumer_id=os.environ[names[3]],
                   capability=os.environ[names[4]],
                   interval=float(os.getenv("AWFL_WORKSPACE_SYNC_INTERVAL_SECONDS", "30")))

    def _read_manifest(self):
        try:
            payload = json.loads((self.root / MANIFEST).read_text())
            if payload.get("version") == 1 and isinstance(payload.get("files"), dict):
                return payload["files"]
        except (OSError, ValueError, AttributeError):
            pass
        return {}

    def _save_manifest(self):
        _atomic_write(self.root / MANIFEST, json.dumps(
            {"version": 1, "files": self.manifest}, sort_keys=True).encode())

    def _scan(self):
        files = {}
        for parent, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and
                       not (Path(parent) / d).is_symlink()]
            for name in names:
                path = Path(parent) / name
                rel = path.relative_to(self.root).as_posix()
                if not allowed_file(rel) or path.is_symlink():
                    continue
                fingerprint = _fingerprint(path, self.manifest.get(rel))
                if fingerprint is not None:
                    files[rel] = fingerprint
                    if len(files) > MAX_FILES:
                        raise RuntimeError("Workspace sync file limit exceeded")
        return files

    async def _broker(self, session):
        if self._access and time.monotonic() < self._access_until:
            return self._access
        async with session.post(
            self.origin + "/consumer-credentials/storage", json=self.identity,
            headers={"x-awfl-consumer-credential": self.capability},
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if not (payload.get("bucket") and payload.get("prefix") and payload.get("accessToken")):
            raise RuntimeError("Workspace token broker returned incomplete credentials")
        expected = os.environ.get("AWFL_WORKSPACE_GCS_BUCKET")
        if expected and payload["bucket"] != expected:
            raise RuntimeError("Workspace token bucket mismatch")
        if self.bucket and (payload["bucket"], payload["prefix"]) != (self.bucket, self.prefix):
            raise RuntimeError("Workspace storage boundary changed unexpectedly")
        self.bucket, self.prefix = payload["bucket"], payload["prefix"]
        self._access = payload["accessToken"]
        self._access_until = time.monotonic() + max(1, min(480, int(payload.get("expiresIn", 600)) - 90))
        return self._access

    async def _list(self, session, token):
        objects = {}
        page = None
        while True:
            params = {"prefix": self.prefix, "fields": "items(name,generation,size,metadata),nextPageToken", "maxResults": "1000"}
            if page:
                params["pageToken"] = page
            url = f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o"
            async with session.get(url, params=params, headers={"Authorization": "Bearer " + token}) as response:
                response.raise_for_status()
                data = await response.json()
            for item in data.get("items", []):
                name = item["name"]
                if not name.startswith(self.prefix):
                    raise RuntimeError("GCS returned an object outside the authorized prefix")
                rel = name[len(self.prefix):]
                if allowed_file(rel):
                    mode = (item.get("metadata") or {}).get("awflMode", "644")
                    objects[rel] = {"generation": str(item["generation"]),
                                    "size": int(item.get("size", 0)),
                                    "mode": int(mode, 8) if re.fullmatch(r"[0-7]{3,4}", mode) else 0o644}
                    if len(objects) > MAX_FILES:
                        raise RuntimeError("Remote workspace file limit exceeded")
            page = data.get("nextPageToken")
            if not page:
                return objects

    def _object_url(self, rel):
        name = self.prefix + rel
        return (f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o/"
                f"{quote(name, safe='')}")

    async def _download(self, session, token, rel, generation):
        url = self._object_url(rel).replace("/storage/v1/", "/download/storage/v1/")
        async with session.get(url, params={"alt": "media", "generation": generation},
                               headers={"Authorization": "Bearer " + token}) as response:
            response.raise_for_status()
            if response.content_length and response.content_length > MAX_FILE_BYTES:
                raise RuntimeError("Remote workspace file exceeds size limit")
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise RuntimeError("Remote workspace file exceeds size limit")
                chunks.append(chunk)
            return b"".join(chunks)

    async def _upload(self, session, token, rel, content, generation, mode=0o644):
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{quote(self.bucket, safe='')}/o"
        params = {"uploadType": "multipart", "ifGenerationMatch": generation or "0"}
        # Preserve executable flags (scripts/tools) across pod restarts.
        # JSON API multipart uploads store metadata and bytes atomically.
        boundary = "awfl-workspace-" + os.urandom(12).hex()
        metadata = json.dumps({"name": self.prefix + rel,
                               "metadata": {"awflMode": format(mode & 0o777, "o")}}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() +
                metadata + f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() +
                content + f"\r\n--{boundary}--\r\n".encode())
        async with session.post(url, params=params, data=body,
                                headers={"Authorization": "Bearer " + token,
                                         "Content-Type": "multipart/related; boundary=" + boundary}) as response:
            response.raise_for_status()
            return str((await response.json())["generation"])

    async def _delete(self, session, token, rel, generation):
        async with session.delete(self._object_url(rel), params={"ifGenerationMatch": generation},
                                  headers={"Authorization": "Bearer " + token}) as response:
            response.raise_for_status()

    async def sync_once(self):
        async with self.lock:
            timeout = aiohttp.ClientTimeout(total=45, sock_connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                token = await self._broker(session)
                remote = await self._list(session, token)  # Never mutate on list failure.
                local = await asyncio.to_thread(self._scan)
                changed = 0
                conflicts = []
                # Restore independent cold-start files concurrently after the
                # regular generation-safe merge; keep steady-state logic intact.
                pending_restores = []
                for rel in sorted(set(remote) | set(local) | set(self.manifest)):
                    baseline = self.manifest.get(rel)
                    r = remote.get(rel)
                    l = local.get(rel)
                    previous_generation = baseline.get("generation") if baseline else None
                    previous_hash = baseline.get("sha256") if baseline else None
                    if r and not l:
                        if (self.root / rel).exists() or (self.root / rel).is_symlink():
                            # A file larger than our limit, or replaced by a
                            # symlink, is NOT an intentional local deletion.
                            conflicts.append(rel)
                            continue
                        if baseline and r["generation"] == previous_generation:
                            await self._delete(session, token, rel, previous_generation)
                            self.manifest.pop(rel, None)
                        elif baseline:
                            conflicts.append(rel)
                        elif r["size"] <= MAX_FILE_BYTES:
                            pending_restores.append((rel, r))
                        continue
                    if r and l:
                        if not baseline or (r["generation"] != previous_generation and l["sha256"] != previous_hash):
                            conflicts.append(rel)
                            continue
                        if r["generation"] != previous_generation:
                            if r["size"] <= MAX_FILE_BYTES:
                                content = await self._download(session, token, rel, r["generation"])
                                target = _safe_target(self.root, rel)
                                current = await asyncio.to_thread(_fingerprint, target)
                                if current and current["sha256"] == l["sha256"]:
                                    await asyncio.to_thread(_atomic_write, target, content, r["mode"])
                                    fingerprint = await asyncio.to_thread(_fingerprint, target)
                                    self.manifest[rel] = {**fingerprint, "generation": r["generation"]}
                                    changed += 1
                                else:
                                    conflicts.append(rel)
                            continue
                        if l["sha256"] != previous_hash or l["mode"] != baseline.get("mode", l["mode"]):
                            content = await asyncio.to_thread(_safe_target(self.root, rel).read_bytes)
                            if len(content) > MAX_FILE_BYTES:
                                continue
                            new_gen = await self._upload(session, token, rel, content, previous_generation, l["mode"])
                            self.manifest[rel] = {**l, "sha256": digest(content), "generation": new_gen}
                            changed += 1
                        continue
                    if l and not r:
                        # New local file, or a remote deletion with local edits.
                        if baseline and l["sha256"] == previous_hash:
                            target = _safe_target(self.root, rel)
                            current = await asyncio.to_thread(_fingerprint, target)
                            if current and current["sha256"] == previous_hash:
                                target.unlink()
                                self.manifest.pop(rel, None)
                                changed += 1
                            continue
                        content = await asyncio.to_thread(_safe_target(self.root, rel).read_bytes)
                        if len(content) > MAX_FILE_BYTES:
                            continue
                        new_gen = await self._upload(session, token, rel, content, None, l["mode"])
                        self.manifest[rel] = {**l, "sha256": digest(content), "generation": new_gen}
                        changed += 1
                    elif baseline:
                        self.manifest.pop(rel, None)

                restore_errors = []
                if pending_restores:
                    semaphore = asyncio.Semaphore(STARTUP_RESTORE_CONCURRENCY)

                    async def restore_one(rel, record):
                        async with semaphore:
                            content = await self._download(session, token, rel, record["generation"])
                            target = _safe_target(self.root, rel)
                            # Don't overwrite files created after the scan.
                            if target.exists() or target.is_symlink():
                                return rel, None
                            await asyncio.to_thread(_atomic_write, target, content, record["mode"])
                            fingerprint = await asyncio.to_thread(_fingerprint, target)
                            return rel, {**fingerprint, "generation": record["generation"]}

                    # Keep successful entries when individual requests fail,
                    # so a retry can pick up without false local conflicts.
                    results = await asyncio.gather(
                        *(restore_one(rel, record) for rel, record in pending_restores),
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, BaseException):
                            restore_errors.append(result)
                        else:
                            rel, entry = result
                            if entry is None:
                                conflicts.append(rel)
                            else:
                                self.manifest[rel] = entry
                                changed += 1
                await asyncio.to_thread(self._save_manifest)
                if restore_errors:
                    raise RuntimeError(
                        f"Workspace restore failed for {len(restore_errors)} object(s)"
                    ) from restore_errors[0]
                if conflicts:
                    raise RuntimeError(f"Workspace sync found {len(conflicts)} conflict(s): {', '.join(conflicts[:5])}")
                return changed

    async def start(self):
        if self.task:
            return
        started = time.monotonic()
        changed = await self.sync_once()  # Hydrate BEFORE accepting project tool commands.
        print(f"[awfl] Initial GCS workspace sync: {changed} files changed in "
              f"{time.monotonic() - started:.2f}s", flush=True)
        self.task = asyncio.create_task(self._periodic(), name="awfl-workspace-gcs-sync")

    async def _periodic(self):
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.sync_once()
            except Exception as error:
                print(f"[awfl] Workspace sync failed: {type(error).__name__}: {error}", flush=True)

    async def close(self, *, final_sync=True):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if final_sync:
            await asyncio.wait_for(self.sync_once(), timeout=25)
