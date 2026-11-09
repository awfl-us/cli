"""The shared AWFL project's asynchronous shell and filesystem runtime.

No separate GKE Job, daemon or tool backend is created. Project MCP tool
events arrive over the existing SSE transport and reuse this runtime.
"""
from __future__ import annotations

import asyncio
import base64
import codecs
import fcntl
import os
from pathlib import Path
import pty
import resource
import signal
import struct
import termios
import uuid

from .file_tools import read_file, write_file, search_files, list_directory
from awfl.consumer_credentials import subprocess_credentials
from .patch import apply_patch


def child_limits():
    resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))


class Runtime:
    def __init__(self, root, *, output_limit=32 * 1024 * 1024, concurrency_limit=4):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cwd = self.root  # Backward-compatible attribute; never mutated by commands.
        self.executions = {}
        self.sessions = {}  # Optional named interactive sessions, scoped to this consumer.
        self.output_limit = output_limit
        self.concurrency_limit = concurrency_limit

    def path(self, value=None):
        p = (self.root / (value or ".")).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Path must remain inside the workspace")
        return p

    def open_file(self, value, flags, create_parents=False):
        path = self.path(value)
        parts = path.relative_to(self.root).parts
        if not parts:
            raise ValueError("Expected file path")
        directory = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                if create_parents:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            return os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        finally:
            os.close(directory)

    async def invoke(self, name, args, *, on_chunk=None):
        if name == "environment_status":
            return {
                "state": "ready", "cwd": str(self.root),
                "running": sum(x["state"] == "running" for x in self.executions.values()),
                "capacity": self.concurrency_limit,
                "capabilities": [
                    "shell", "command_input", "command_output", "process_groups",
                    "pty", "file_patch", "file_search", "file_metadata", "python",
                    "node", "git", "playwright", "chromium", "https",
                ],
            }
        if name == "run_command":
            if sum(x["state"] == "running" for x in self.executions.values()) >= self.concurrency_limit:
                raise ValueError("Concurrent command limit reached")
            if len(self.executions) >= 128:
                raise ValueError("Workspace execution limit (128) reached")
            cwd = self.path(args.get("cwd"))
            if not cwd.is_dir():
                raise ValueError("Working directory does not exist")
            session_id = args.get("session_id")
            if session_id and session_id in self.sessions:
                existing_id = self.sessions[session_id]
                return {"execution_id": existing_id, "session_id": session_id,
                        **self.output(existing_id, 0)}
            execution_id = args.get("execution_id") or uuid.uuid4().hex
            if not isinstance(execution_id, str) or not execution_id or len(execution_id) > 128:
                raise ValueError("Invalid execution ID")
            if execution_id in self.executions:
                return {"execution_id": execution_id, **self.output(execution_id, 0)}
            timeout = args.get("timeout_ms")
            if timeout is not None:
                timeout = timeout / 1000
            else:
                timeout = args.get("timeout", 120)
            if timeout is not None and not 0 < timeout <= 3600:
                raise ValueError("Command timeout out of range")
            interactive = bool(args.get("interactive", False) or args.get("pty", False))
            use_pty = bool(args.get("pty", False))
            item = {
                "state": "running", "exit_code": None, "chunks": [], "bytes": 0,
                "truncated": False, "reason": None, "cwd": str(cwd),
                "pty": use_pty, "session_id": session_id,
                "changed": asyncio.Event(), "on_chunk": on_chunk,
            }
            self.executions[execution_id] = item
            if session_id:
                self.sessions[session_id] = execution_id
            # The existing project's user credentials are inherited. Internal
            # execution control variables must not be propagated to subprocesses.
            env = subprocess_credentials({
                k: v for k, v in os.environ.items() if not k.startswith("AWFL_EXECUTION_")
            })
            master = slave = None
            try:
                if use_pty:
                    master, slave = pty.openpty()
                    self._set_pty_size(master, args.get("cols", 80), args.get("rows", 24))
                    process = await asyncio.create_subprocess_exec(
                        "/bin/bash", "-c", args["command"], cwd=cwd, env=env,
                        stdin=slave, stdout=slave, stderr=slave,
                        start_new_session=True, preexec_fn=child_limits,
                    )
                    os.close(slave)
                    slave = None
                    item["pty_master"] = master
                else:
                    process = await asyncio.create_subprocess_exec(
                        "/bin/bash", "-c", args["command"], cwd=cwd, env=env,
                        stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        start_new_session=True, preexec_fn=child_limits,
                    )
                item["process"] = process
                item["task"] = asyncio.create_task(self._collect(item, timeout))
            except Exception:
                del self.executions[execution_id]
                if session_id:
                    self.sessions.pop(session_id, None)
                for descriptor in (master, slave):
                    if descriptor is not None:
                        os.close(descriptor)
                raise
            yield_ms = args.get("yield_time_ms", 500)
            if not isinstance(yield_ms, int) or not 0 <= yield_ms <= 60000:
                raise ValueError("yield_time_ms out of range")
            if yield_ms:
                await asyncio.wait({item["task"]}, timeout=yield_ms / 1000)
            result = self.output(execution_id, 0, max_bytes=args.get("max_output_bytes", 65536))
            return {"execution_id": execution_id, "cwd": str(cwd), "pty": use_pty,
                    "session_id": session_id, **result}
        if name == "command_output":
            execution_id = args["execution_id"]
            cursor = args.get("cursor", 0)
            item = self.executions[execution_id]
            if cursor == len(item["chunks"]) and item["state"] == "running" and args.get("wait_ms", 0):
                item["changed"].clear()
                # Avoid missed wakeups when a chunk lands just before clear.
                if cursor == len(item["chunks"]) and item["state"] == "running":
                    try:
                        await asyncio.wait_for(item["changed"].wait(), args["wait_ms"] / 1000)
                    except asyncio.TimeoutError:
                        pass
            return {"execution_id": execution_id, **self.output(
                execution_id, cursor, max_bytes=args.get("max_bytes", 65536))}
        if name == "command_cancel":
            item = self.executions[args["execution_id"]]
            if item["state"] == "running":
                item["reason"] = "cancelled"
                self._signal(item, signal.SIGTERM)
                try:
                    await asyncio.wait_for(asyncio.shield(item["task"]), timeout=2)
                except asyncio.TimeoutError:
                    self._kill(item)
                    await item["task"]
            return {"execution_id": args["execution_id"], **self.output(
                args["execution_id"], args.get("cursor", 0))}
        if name == "command_input":
            item = self.executions[args["execution_id"]]
            if item["state"] != "running":
                raise ValueError("Execution is not running")
            chosen_signal = args.get("signal")
            if chosen_signal:
                if chosen_signal not in ("SIGINT", "SIGTERM"):
                    raise ValueError("Only SIGINT and SIGTERM are supported")
                self._signal(item, getattr(signal, chosen_signal))
            if "input" in args or "input_base64" in args:
                if "input" in args and "input_base64" in args:
                    raise ValueError("Provide input or input_base64, not both")
                data = (base64.b64decode(args["input_base64"], validate=True)
                        if "input_base64" in args else args["input"].encode("utf-8"))
                if item["pty"]:
                    os.write(item["pty_master"], data)
                elif item["process"].stdin is not None:
                    item["process"].stdin.write(data)
                    await item["process"].stdin.drain()
                else:
                    raise ValueError("Command was not started in interactive mode")
            if args.get("eof"):
                if item["pty"]:
                    os.write(item["pty_master"], b"\x04")
                elif item["process"].stdin is not None:
                    item["process"].stdin.close()
            if item["pty"] and ("cols" in args or "rows" in args):
                self._set_pty_size(item["pty_master"], args.get("cols", 80), args.get("rows", 24))
            return {"execution_id": args["execution_id"], "state": item["state"], "accepted": True}
        if name == "read_file":
            return await asyncio.to_thread(read_file, self, args)
        if name == "write_file":
            return await asyncio.to_thread(write_file, self, args)
        if name == "apply_patch":
            root = self.path(args.get("cwd"))
            return await asyncio.to_thread(
                apply_patch, root, args["patch"], atomic=args.get("atomic", True),
                expected_hashes=args.get("expected_hashes"))
        if name == "search_files":
            return await asyncio.to_thread(search_files, self, args)
        if name == "list_directory":
            return await asyncio.to_thread(list_directory, self, args)
        raise ValueError("Unknown tool")

    @staticmethod
    def _set_pty_size(descriptor, cols, rows):
        if not isinstance(cols, int) or not isinstance(rows, int) or not 1 <= cols <= 1000 or not 1 <= rows <= 1000:
            raise ValueError("PTY dimensions out of range")
        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    @staticmethod
    def _signal(item, signum):
        try:
            os.killpg(item["process"].pid, signum)
        except ProcessLookupError:
            pass

    def _kill(self, item):
        self._signal(item, signal.SIGKILL)

    @staticmethod
    def _redact_known_credentials(text):
        # Tool output is persisted to Firestore. Replace identifiable tokens
        # before returning them or sending output callbacks. This does not
        # replace OS-level credential isolation for arbitrary shell commands.
        secrets = []
        for key in ("GITHUB_TOKEN", "GH_TOKEN", "FIREBASE_ID_TOKEN",
                    "FIREBASE_CUSTOM_TOKEN", "AWFL_PROJECT_LOCK_TOKEN",
                    "AWFL_CREDENTIAL_REFRESH_CAPABILITY",
                    "CLOUDSDK_AUTH_ACCESS_TOKEN"):
            token = os.environ.get(key)
            if token and len(token) >= 8:
                secrets.append(token)
        token_file = os.environ.get("AWFL_GITHUB_TOKEN_FILE")
        if token_file:
            try:
                secret = Path(token_file).read_text().strip()
                if len(secret) >= 8:
                    secrets.append(secret)
            except OSError:
                pass
        for secret in set(secrets):
            text = text.replace(secret, "[REDACTED]")
        return text

    async def _append_chunk(self, item, stream, text):
        if not text:
            return
        text = self._redact_known_credentials(text)
        if item["bytes"] >= self.output_limit:
            item["truncated"] = True
            item["reason"] = "output_limit"
            self._kill(item)
            return
        raw = text.encode("utf-8")
        available = self.output_limit - item["bytes"]
        clipped = raw[:available].decode("utf-8", errors="ignore")
        if clipped:
            cursor = len(item["chunks"]) + 1
            event = {"cursor": cursor, "stream": stream, "text": clipped}
            item["chunks"].append(event)
            item["bytes"] += len(clipped.encode("utf-8"))
            item["changed"].set()
            if item["on_chunk"]:
                # The callback persists output on the existing MCP request. Its
                # errors must not prevent subprocess I/O from draining.
                try:
                    await item["on_chunk"](event)
                except Exception:
                    pass
        if len(raw) > available:
            item["truncated"] = True
            item["reason"] = "output_limit"
            self._kill(item)

    async def _collect(self, item, timeout):
        async def drain(stream, label):
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                data = await stream.read(8192)
                if not data:
                    await self._append_chunk(item, label, decoder.decode(b"", final=True))
                    return
                await self._append_chunk(item, label, decoder.decode(data))

        async def drain_pty(master):
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                try:
                    data = await asyncio.to_thread(os.read, master, 8192)
                except OSError:
                    data = b""
                if not data:
                    await self._append_chunk(item, "pty", decoder.decode(b"", final=True))
                    return
                await self._append_chunk(item, "pty", decoder.decode(data))

        if item["pty"]:
            tasks = [asyncio.create_task(drain_pty(item["pty_master"]))]
        else:
            tasks = [
                asyncio.create_task(drain(item["process"].stdout, "stdout")),
                asyncio.create_task(drain(item["process"].stderr, "stderr")),
            ]
        try:
            if timeout is None:
                await item["process"].wait()
            else:
                async with asyncio.timeout(timeout):
                    await item["process"].wait()
            self._kill(item)  # Kill any background children left by the shell.
            await asyncio.gather(*tasks)
        except TimeoutError:
            item["reason"] = "timeout"
            self._kill(item)
            await item["process"].wait()
        finally:
            self._kill(item)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if item.get("pty_master") is not None:
                os.close(item["pty_master"])
            item["exit_code"] = item["process"].returncode
            item["state"] = ("timed_out" if item["reason"] == "timeout"
                             else "cancelled" if item["reason"] == "cancelled"
                             else "completed")
            item["changed"].set()

    def output(self, execution_id, cursor, *, max_bytes=65536):
        item = self.executions[execution_id]
        if not isinstance(cursor, int) or cursor < 0 or cursor > len(item["chunks"]):
            raise ValueError("Invalid output cursor")
        if not isinstance(max_bytes, int) or not 1 <= max_bytes <= 1024 * 1024:
            raise ValueError("max_bytes out of range")
        chunks = []
        used = 0
        for event in item["chunks"][cursor:]:
            size = len(event["text"].encode("utf-8"))
            if chunks and used + size > max_bytes:
                break
            chunks.append(event)
            used += size
            if len(chunks) >= 128:
                break
        next_cursor = cursor + len(chunks)
        return {
            "state": item["state"], "exit_code": item["exit_code"],
            "truncated": item["truncated"], "reason": item["reason"],
            "chunks": chunks, "next_cursor": next_cursor, "cursor": next_cursor,
            "has_more": next_cursor < len(item["chunks"]),
            "cwd": item["cwd"], "pty": item["pty"],
            "stdout": "".join(x["text"] for x in chunks if x["stream"] == "stdout"),
            "stderr": "".join(x["text"] for x in chunks if x["stream"] == "stderr"),
            "error": None,
        }

    async def close(self):
        for item in self.executions.values():
            self._kill(item)
        await asyncio.gather(*(x["task"] for x in self.executions.values()), return_exceptions=True)
