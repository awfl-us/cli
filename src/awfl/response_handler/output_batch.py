"""Bounded, ordered batched persistence for an existing MCP execution.

The subprocess reader never waits for one network round trip per chunk. A
single background writer groups adjacent output events into an authenticated
callback, while retaining Firestore's original per-event stable cursors.

The queue provides backpressure if the server is slow. Finishing waits for
all successful writes (or reports incomplete output), so a terminal callback
never silently outruns pending stdout/stderr persistence.
"""
import asyncio
from typing import Awaitable, Callable

PostCallback = Callable[[str, dict], Awaitable[bool]]


class OutputBatchSink:
    MAX_EVENTS = 16
    MAX_BYTES = 128 * 1024
    FLUSH_SECONDS = 0.025
    QUEUE_CAPACITY = 64

    def __init__(self, callback_id: str, execution_id: str,
                 post: PostCallback, *, flush_seconds: float = FLUSH_SECONDS):
        self.callback_id = callback_id
        self.execution_id = execution_id
        self.post = post
        self.flush_seconds = flush_seconds
        self.queue = asyncio.Queue(maxsize=self.QUEUE_CAPACITY)
        self.persisted = set()
        self.batch_calls = 0
        self.fallback_calls = 0
        self._done = object()
        self._closed = False
        self._writer = asyncio.create_task(self._run(), name="mcp-output-batch-" + execution_id)

    async def append(self, event: dict):
        if self._closed:
            raise RuntimeError("Output already finalized")
        # A full queue intentionally applies backpressure to the subprocess.
        # It never drops output or creates unbounded memory growth.
        await self.queue.put(event)

    async def finish(self):
        if not self._closed:
            self._closed = True
            await self.queue.put(self._done)
        await self._writer
        return self.persisted

    async def _safe_post(self, payload):
        try:
            return await self.post(self.callback_id, payload) is True
        except Exception:
            return False

    async def _flush(self, batch):
        self.batch_calls += 1
        ok = await self._safe_post({
            "_awfl_mcp_event": "output_batch",
            "execution_id": self.execution_id,
            "chunks": batch,
        })
        if ok:
            self.persisted.update(e["cursor"] for e in batch)
            return
        # A 409 from an older server or transient batch error must not
        # silently lose output. Legacy per-event callbacks remain available.
        # Duplicate prefixes are accepted by the authoritative cursor store.
        for event in batch:
            self.fallback_calls += 1
            ok = await self._safe_post({
                "_awfl_mcp_event": "output",
                "execution_id": self.execution_id,
                "chunk": event,
            })
            if ok:
                self.persisted.add(event["cursor"])

    async def _run(self):
        held = None
        finishing = False
        loop = asyncio.get_running_loop()
        while True:
            if held is not None:
                first, held = held, None
            else:
                first = await self.queue.get()
            if first is self._done:
                return
            batch = [first]
            size = len(first["text"].encode("utf-8"))
            deadline = loop.time() + self.flush_seconds
            while len(batch) < self.MAX_EVENTS:
                try:
                    item = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        item = await asyncio.wait_for(self.queue.get(), remaining)
                    except asyncio.TimeoutError:
                        break
                if item is self._done:
                    finishing = True
                    break
                next_size = len(item["text"].encode("utf-8"))
                if size + next_size > self.MAX_BYTES:
                    held = item
                    break
                batch.append(item)
                size += next_size
            await self._flush(batch)
            if finishing and held is None:
                return
