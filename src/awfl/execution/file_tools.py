"""Filesystem operations used by the shared AWFL execution runtime."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import fnmatch
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from .patch import PatchConflict


def file_hash(data):
    return hashlib.sha256(data).hexdigest()


def _metadata(path: Path, data: bytes | None = None) -> dict:
    info = path.stat()
    return {
        "path": str(path),
        "size": info.st_size,
        "mtime": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
        "permissions": oct(stat.S_IMODE(info.st_mode)),
        "sha256": file_hash(data if data is not None else path.read_bytes()),
    }


def read_file(runtime, args: dict) -> dict:
    path = runtime.path(args["path"])
    if not path.is_file():
        raise ValueError("File not found or not regular")
    fd = runtime.open_file(args["path"], os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if args.get("expected_hash") is not None:
            f.seek(0)
            actual = file_hash(f.read())
            if actual != args["expected_hash"]:
                raise PatchConflict("Stale file content hash")
        encoding = args.get("encoding", "utf-8")
        if encoding not in ("utf-8", "base64"):
            raise ValueError("encoding must be utf-8 or base64")
        limit = args.get("max_bytes", 32768)
        if not isinstance(limit, int) or not 1 <= limit <= 1024 * 1024:
            raise ValueError("max_bytes out of range")
        if "start_line" in args or "end_line" in args:
            if encoding != "utf-8":
                raise ValueError("Line ranges require UTF-8")
            f.seek(0)
            full = f.read()
            text = full.decode("utf-8")
            lines = text.splitlines(keepends=True)
            start = args.get("start_line", 1)
            end = args.get("end_line", len(lines))
            if not isinstance(start, int) or start < 1 or not isinstance(end, int) or end < start:
                raise ValueError("Invalid line range")
            segment = lines[start - 1:end]
            if args.get("include_line_numbers", False):
                value = "".join(f"{i}: {line}" for i, line in enumerate(segment, start))
            else:
                value = "".join(segment)
            raw = value.encode("utf-8")
            used = raw[:limit]
            text_out = used.decode("utf-8", errors="ignore")
            return {
                "text": text_out,
                "start_line": start,
                "end_line": min(end, len(lines)),
                "truncated": len(used) < len(raw),
                **_metadata(path, full),
            }
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or offset < 0 or offset > size:
            raise ValueError("Invalid byte offset")
        f.seek(offset)
        data = f.read(limit + 1)
        raw = data[:limit]
        legacy = not any(k in args for k in
            ("encoding", "include_line_numbers", "expected_hash", "with_metadata"))
        if legacy:
            return {"content": raw.decode("utf-8", errors="replace"),
                    "next_offset": offset + len(raw), "truncated": len(data) > limit}
        text = base64.b64encode(raw).decode("ascii") if encoding == "base64" else raw.decode("utf-8", errors="replace")
        f.seek(0)
        full = f.read()
        return {
            "text": text, "encoding": encoding,
            "next_offset": offset + len(raw), "truncated": len(data) > limit,
            **_metadata(path, full),
        }


def write_file(runtime, args: dict) -> dict:
    path = runtime.path(args["path"])
    content = args["content"]
    encoding = args.get("encoding", "utf-8")
    if encoding == "utf-8" and isinstance(content, str):
        data = content.encode("utf-8")
    elif encoding == "base64" and isinstance(content, str):
        data = base64.b64decode(content, validate=True)
    else:
        raise ValueError("Invalid content or encoding")
    create_only = args.get("create_only", False)
    expected_hash = args.get("expected_hash")
    if expected_hash is not None and (not isinstance(expected_hash, str) or len(expected_hash) != 64):
        raise ValueError("expected_hash must be SHA-256")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise PatchConflict("File is not a regular file")
    before = path.read_bytes() if path.exists() else None
    if create_only and before is not None:
        raise PatchConflict("File already exists")
    if expected_hash is not None and (before is None or file_hash(before) != expected_hash):
        raise PatchConflict("Stale file content hash")
    if not path.parent.exists():
        if args.get("create_parents", True):
            path.parent.mkdir(parents=True)
        else:
            raise ValueError("Parent directory not found")
    # Atomic same-filesystem replacement; never truncate the original first.
    fd, name = tempfile.mkstemp(prefix=".awfl-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        mode = args.get("permissions")
        if mode is not None:
            mode = int(str(mode), 8) if isinstance(mode, str) else mode
            if not isinstance(mode, int) or mode < 0 or mode > 0o777:
                raise ValueError("Invalid permissions")
        elif path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
        else:
            mode = 0o600
        os.chmod(name, mode)
        # Re-check before commit (another writer may have modified the file).
        actual = path.read_bytes() if path.exists() else None
        if actual != before:
            raise PatchConflict("File changed during write")
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
    return {"path": str(path), "bytes": len(data), "sha256": file_hash(data),
            "created": before is None}


def _matches_glob(relative, glob):
    if not glob:
        return True
    if isinstance(glob, str):
        globs = [glob]
    elif isinstance(glob, list) and all(isinstance(x, str) for x in glob):
        globs = glob
    else:
        raise ValueError("glob must be string or list")
    return any(fnmatch.fnmatch(relative, g) or
               (g.startswith("**/") and fnmatch.fnmatch(relative, g[3:])) for g in globs)


def _walk(root: Path, *, recursive=True, include_hidden=False, respect_gitignore=True):
    # Ask Git once instead of running git check-ignore for every directory and
    # file. Includes untracked, non-ignored files; a non-repository falls back
    # to a normal directory walk.
    if respect_gitignore and recursive:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--cached", "--others",
                 "--exclude-standard", "-z"], capture_output=True,
                timeout=10, check=False,
            )
            if result.returncode == 0:
                for raw in sorted(p for p in result.stdout.split(b"\0") if p):
                    relative = os.fsdecode(raw)
                    path = root / relative
                    if not include_hidden and any(p.startswith(".") for p in Path(relative).parts):
                        continue
                    if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                        yield path
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if
            (include_hidden or not d.startswith(".")) and d != ".git")
        for name in sorted(files):
            if not include_hidden and name.startswith("."):
                continue
            path = current_path / name
            if not path.is_symlink() and path.is_file():
                yield path
        if not recursive:
            break


def search_files(runtime, args: dict) -> dict:
    root = runtime.path(args.get("root"))
    if not root.is_dir():
        raise ValueError("Search root must be a directory")
    pattern = args.get("pattern", "")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern is required")
    mode = args.get("mode", "content")
    if mode not in ("content", "filename"):
        raise ValueError("mode must be content or filename")
    regex = args.get("regex", False)
    case_sensitive = args.get("case_sensitive", True)
    limit = args.get("max_results", 100)
    offset = args.get("offset", 0)
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("max_results out of range")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be nonnegative")
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = re.compile(pattern if regex else re.escape(pattern), flags)
    results = []
    found = 0
    for path in _walk(root, recursive=True,
                      include_hidden=args.get("include_hidden", False),
                      respect_gitignore=args.get("respect_gitignore", True)):
        rel = str(path.relative_to(root))
        if not _matches_glob(rel, args.get("glob")):
            continue
        if mode == "filename":
            if compiled.search(rel):
                if found >= offset and len(results) <= limit:
                    results.append({"path": str(path), "relative_path": rel})
                found += 1
        else:
            try:
                # Bounded reads avoid loading arbitrarily large binary files.
                if path.stat().st_size > 16 * 1024 * 1024:
                    continue
                data = path.read_bytes()
                if b"\0" in data[:4096]:
                    continue
                lines = data.decode("utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            context = min(max(args.get("context_lines", 0), 0), 10)
            for i, line in enumerate(lines):
                if compiled.search(line):
                    if found >= offset and len(results) <= limit:
                        results.append({
                            "path": str(path), "relative_path": rel,
                            "line": i + 1, "excerpt": line[:1000],
                            **({"before": lines[max(0, i - context):i],
                                "after": lines[i + 1:i + context + 1]} if context else {}),
                        })
                    found += 1
        if found > offset + limit:
            break
    return {"matches": results[:limit], "offset": offset,
            "next_offset": offset + min(len(results), limit),
            "has_more": found > offset + limit, "truncated": found > offset + limit}


def list_directory(runtime, args: dict) -> dict:
    root = runtime.path(args.get("path"))
    if not root.is_dir():
        raise ValueError("Directory not found")
    recursive = args.get("recursive", False)
    depth = args.get("max_depth", 3) if recursive else 1
    limit = args.get("max_entries", 200)
    if not isinstance(depth, int) or not 1 <= depth <= 16:
        raise ValueError("max_depth out of range")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("max_entries out of range")
    entries = []
    stack = [(root, 0)]
    while stack and len(entries) <= limit:
        directory, level = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except PermissionError as exc:
            raise ValueError("Directory is not readable") from exc
        for path in children:
            if path.name.startswith(".") and not args.get("include_hidden", False):
                continue
            if path.is_symlink():
                kind = "symlink"
            elif path.is_dir():
                kind = "directory"
            elif path.is_file():
                kind = "file"
            else:
                kind = "other"
            info = path.lstat()
            entries.append({"path": str(path), "name": path.name, "type": kind,
                            "size": info.st_size, "permissions": oct(stat.S_IMODE(info.st_mode)),
                            "mtime": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
            if len(entries) > limit:
                break
            if recursive and kind == "directory" and level + 1 < depth:
                stack.append((path, level + 1))
    return {"path": str(root), "entries": entries[:limit], "truncated": len(entries) > limit}
