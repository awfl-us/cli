"""Regression tests for lossless, ordered MCP output batching."""
import asyncio
import unittest

from awfl.response_handler.output_batch import OutputBatchSink


def events(n):
    return [{"cursor": i + 1,
             "stream": ("stderr" if i % 7 == 0 else "stdout"),
             "text": f"line-{i:05d}\n"} for i in range(n)]


class BatchOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_32_events_are_two_ordered_batches_not_32_posts(self):
        calls = []
        async def post(_, body):
            calls.append(body)
            return True
        sink = OutputBatchSink("callback", "exec", post, flush_seconds=.03)
        expected = events(32)
        for chunk in expected:
            await sink.append(chunk)
        persisted = await sink.finish()
        self.assertEqual(persisted, set(range(1, 33)))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(x["_awfl_mcp_event"] == "output_batch" for x in calls))
        self.assertEqual([e for call in calls for e in call["chunks"]], expected)
        self.assertTrue(all(len(call["chunks"]) <= 16 for call in calls))

    async def test_small_interactive_output_flushes_without_waiting_for_completion(self):
        posted = asyncio.Event()
        async def post(_, body):
            posted.set()
            return True
        sink = OutputBatchSink("callback", "exec", post, flush_seconds=.01)
        await sink.append(events(1)[0])
        await asyncio.wait_for(posted.wait(), timeout=.5)
        persisted = await sink.finish()
        self.assertEqual(persisted, {1})

    async def test_batch_rejection_uses_legacy_event_callbacks(self):
        calls = []
        async def post(_, body):
            calls.append(body)
            return body["_awfl_mcp_event"] == "output"
        sink = OutputBatchSink("callback", "exec", post)
        for event in events(17):
            await sink.append(event)
        persisted = await sink.finish()
        self.assertEqual(persisted, set(range(1, 18)))
        self.assertEqual(sink.fallback_calls, 17)
        self.assertEqual(len([x for x in calls if x["_awfl_mcp_event"] == "output_batch"]), 2)

    async def test_failed_callbacks_are_not_reported_persisted(self):
        async def post(_, body):
            return False
        sink = OutputBatchSink("callback", "exec", post)
        for event in events(3):
            await sink.append(event)
        self.assertEqual(await sink.finish(), set())
        self.assertEqual(sink.fallback_calls, 3)

    async def test_large_events_respect_batch_size_and_preserve_cursors(self):
        seen = []
        async def post(_, body):
            seen.append(body["chunks"])
            return True
        sink = OutputBatchSink("callback", "exec", post)
        chunks = [{"cursor": i + 1, "stream": "stdout", "text": "x" * 32768}
                  for i in range(10)]
        for event in chunks:
            await sink.append(event)
        await sink.finish()
        self.assertEqual(len(seen), 3)
        self.assertEqual([e for batch in seen for e in batch], chunks)
        self.assertTrue(all(sum(len(e["text"]) for e in batch) <= 131072 for batch in seen))

    async def test_finish_blocks_until_server_acknowledges(self):
        ack = asyncio.Event()
        started = asyncio.Event()
        async def post(_, body):
            started.set()
            await ack.wait()
            return True
        sink = OutputBatchSink("callback", "exec", post, flush_seconds=0)
        await sink.append(events(1)[0])
        done = asyncio.create_task(sink.finish())
        await started.wait()
        self.assertFalse(done.done())
        ack.set()
        self.assertEqual(await asyncio.wait_for(done, 1), {1})


if __name__ == "__main__":
    unittest.main()
