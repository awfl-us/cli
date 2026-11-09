"""Contextual multi-file patching for the existing AWFL project consumer.

All hunks are validated before touching the filesystem. Changes are staged
beside their targets and rollback restores the original bytes on commit errors.
This is filesystem-level best-effort atomicity, not a cross-file OS transaction.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile


class PatchConflict(ValueError):
    """A patch cannot be applied safely without guessing."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_path(root: Path, value: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PatchConflict("Invalid patch path")
    candidate = Path(value)
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        if ".." in candidate.parts:
            raise PatchConflict("Patch path may not traverse parent directories")
        candidate = (root / candidate).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise PatchConflict("Patch path must be within workspace")
    # Reject intermediate and final symlinks: another process must not redirect writes.
    cur = root
    for part in candidate.relative_to(root).parts:
        cur = cur / part
        if cur.is_symlink():
            raise PatchConflict("Symbolic links are not patch targets")
    return candidate, str(candidate.relative_to(root))


def _parse(patch: str) -> list[dict]:
    if not isinstance(patch, str):
        raise PatchConflict("Patch must be text")
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise PatchConflict("Expected *** Begin Patch and *** End Patch")
    actions = []
    active = None
    current_hunk = None
    for line in lines[1:-1]:
        kinds = {
            "*** Add File: ": "added",
            "*** Update File: ": "modified",
            "*** Delete File: ": "deleted",
        }
        matched = next(((prefix, kind) for prefix, kind in kinds.items() if line.startswith(prefix)), None)
        if matched:
            prefix, kind = matched
            active = {"kind": kind, "path": line[len(prefix):], "move": None,
                      "hunks": [], "added": []}
            actions.append(active)
            current_hunk = None
            continue
        if active is None:
            raise PatchConflict("Patch body must start with a file operation")
        if line.startswith("*** Move to: "):
            if active["kind"] != "modified" or active["move"] is not None:
                raise PatchConflict("Move to requires a single Update File action")
            active["move"] = line[len("*** Move to: "):]
        elif line.startswith("@@"):
            if active["kind"] != "modified":
                raise PatchConflict("Hunks only apply to Update File")
            current_hunk = []
            active["hunks"].append(current_hunk)
        elif line == "*** End of File":
            continue
        elif active["kind"] == "added":
            if not line.startswith("+"):
                raise PatchConflict("Add File content must have + prefixes")
            active["added"].append(line[1:])
        elif active["kind"] == "modified" and current_hunk is not None and line.startswith((" ", "-", "+")):
            current_hunk.append((line[0], line[1:]))
        else:
            raise PatchConflict("Unexpected patch line: " + line[:100])
    if not actions:
        raise PatchConflict("Patch has no file operations")
    return actions


def _newline(lines: list[bytes]) -> bytes:
    for line in lines:
        if line.endswith(b"\r\n"):
            return b"\r\n"
        if line.endswith(b"\n"):
            return b"\n"
    return b"\n"


def _line_data(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith((b"\n", b"\r")):
        return line[:-1]
    return line


def _apply_hunks(original: bytes, hunks: list[list[tuple[str, str]]]) -> bytes:
    try:
        original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchConflict("Cannot context-patch a non-UTF-8 file") from exc
    lines = original.splitlines(keepends=True)
    newline = _newline(lines)
    cursor = 0
    for hunk in hunks:
        if not hunk:
            raise PatchConflict("Empty patch hunk")
        old = [text.encode("utf-8") for kind, text in hunk if kind in (" ", "-")]
        if not old and lines:
            raise PatchConflict("Insertion without context is ambiguous")
        if not old:
            candidates = [0]
        else:
            candidates = [
                pos for pos in range(cursor, len(lines) - len(old) + 1)
                if all(_line_data(lines[pos + idx]) == value for idx, value in enumerate(old))
            ]
        if len(candidates) != 1:
            raise PatchConflict("Patch context not unique or not found")
        at = candidates[0]
        replacements = []
        previous = at
        for kind, text in hunk:
            if kind == " ":
                replacements.append(lines[previous])
                previous += 1
            elif kind == "-":
                previous += 1
            else:
                replacements.append(text.encode("utf-8") + newline)
        if replacements and at and not lines[at - 1].endswith((b"\r", b"\n")):
            replacements[0] = newline + replacements[0]
        lines[at:at + len(old)] = replacements
        cursor = at + len(replacements)
    return b"".join(lines)


def apply_patch(root: str | Path, patch: str, *, atomic: bool = True,
                expected_hashes: dict | None = None) -> dict:
    """Apply a patch with preflight validation, staging, conflict checks and rollback.

    Always uses the safe atomic path, including when atomic=False; this flag is
    accepted for API compatibility but never relaxes file-integrity guarantees.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise PatchConflict("Workspace root does not exist")
    if not isinstance(atomic, bool):
        raise PatchConflict("atomic must be boolean")
    actions = _parse(patch)
    expected_hashes = expected_hashes or {}
    if not isinstance(expected_hashes, dict):
        raise PatchConflict("expected_hashes must be an object")
    expected = {}
    for path, sha in expected_hashes.items():
        _, rel = _clean_path(root, path)
        if not isinstance(sha, str) or len(sha) != 64:
            raise PatchConflict("expected_hashes values must be SHA-256 hashes")
        expected[rel] = sha.lower()

    original = {}
    original_modes = {}
    planned = {}
    results = []
    affected = set()

    def fetch(path: Path, rel: str) -> bytes | None:
        if path in original:
            return original[path]
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PatchConflict("Patch target must be a regular file")
        data = path.read_bytes() if path.exists() else None
        if rel in expected and (data is None or _digest(data) != expected[rel]):
            raise PatchConflict("Stale content hash for " + rel)
        original[path] = data
        original_modes[path] = stat.S_IMODE(path.stat().st_mode) if data is not None else None
        return data

    for action in actions:
        path, rel = _clean_path(root, action["path"])
        if path in affected:
            raise PatchConflict("File targeted multiple times: " + rel)
        affected.add(path)
        old = fetch(path, rel)
        kind = action["kind"]
        if kind == "added":
            if old is not None:
                raise PatchConflict("File already exists: " + rel)
            new = ("\n".join(action["added"]) + "\n").encode("utf-8") if action["added"] else b""
            planned[path] = new
        elif kind == "deleted":
            if old is None:
                raise PatchConflict("File does not exist: " + rel)
            planned[path] = None
            new = b""
        else:
            if old is None:
                raise PatchConflict("File does not exist: " + rel)
            new = _apply_hunks(old, action["hunks"]) if action["hunks"] else old
            target = path
            if action["move"]:
                target, target_rel = _clean_path(root, action["move"])
                if target in affected or fetch(target, target_rel) is not None:
                    raise PatchConflict("Move destination already exists: " + target_rel)
                affected.add(target)
                planned[path] = None
                planned[target] = new
            else:
                planned[path] = new
        results.append({
            "path": rel, "operation": "moved" if action["move"] else kind,
            **({"new_path": str(target.relative_to(root))} if action["move"] else {}),
            "added_lines": sum(1 for h in action["hunks"] for op, _ in h if op == "+")
                if kind == "modified" else len(action["added"]) if kind == "added" else 0,
            "removed_lines": sum(1 for h in action["hunks"] for op, _ in h if op == "-")
                if kind == "modified" else len(old.splitlines()) if kind == "deleted" else 0,
            "sha256": _digest(new) if kind != "deleted" else None,
        })

    # Recheck every target immediately before mutation, catching concurrent edits.
    for path, before in original.items():
        current = path.read_bytes() if path.exists() and path.is_file() else None
        if current != before:
            raise PatchConflict("File changed during patch validation: " + str(path.relative_to(root)))

    staged = {}
    created_dirs = []
    changed = []
    try:
        for path, value in planned.items():
            if value is None:
                continue
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            fd, temp_name = tempfile.mkstemp(prefix=".awfl-patch-", dir=path.parent)
            staged[path] = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            source = original.get(path)
            if source is not None:
                os.chmod(temp_name, stat.S_IMODE(path.stat().st_mode))
            elif path in affected:
                # For moves, preserve original mode at the source path.
                moved = next((a for a in actions if a["move"] and
                              (root / a["move"]).resolve() == path), None)
                if moved:
                    src, _ = _clean_path(root, moved["path"])
                    os.chmod(temp_name, stat.S_IMODE(src.stat().st_mode))
        for path in sorted(planned, key=str):
            value = planned[path]
            if value is None:
                if path.exists():
                    path.unlink()
            elif original.get(path) is None:
                # link() is atomic create-only: never overwrite a file that
                # appeared after preflight (including on an Add or Move).
                os.link(staged[path], path, follow_symlinks=False)
            else:
                os.replace(staged[path], path)
            changed.append(path)
    except Exception:
        # Restore all originals, including deleted files. Original snapshots
        # are the authoritative rollback data; temp artifacts are cleaned below.
        for path in reversed(changed):
            before = original.get(path)
            if before is None:
                path.unlink(missing_ok=True)
            else:
                fd, backup = tempfile.mkstemp(prefix=".awfl-rollback-", dir=path.parent)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(before)
                os.chmod(backup, original_modes[path])
                os.replace(backup, path)
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
    return {"ok": True, "atomic": True, "files": results}
