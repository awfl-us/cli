"""Cloud workspace hydration gates the command-capable SSE stream."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from awfl.consumer.sse_consumer import restore_workspace_before_events
from test_workspace_sync import MemoryBucket


class WorkspaceStartupBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bucket = MemoryBucket(self.root)

    async def test_no_ready_signal_until_gcs_download_completes(self):
        self.bucket.objects['AGENT.md'] = ('101', b'Project-specific instructions', 0o644)
        started = asyncio.Event()
        allow_download = asyncio.Event()
        actual_download = self.bucket._download

        async def blocked_download(*args):
            started.set()
            await allow_download.wait()
            return await actual_download(*args)

        ready = asyncio.Event()
        with patch.object(self.bucket, '_download', side_effect=blocked_download):
            restore = asyncio.create_task(
                restore_workspace_before_events(self.bucket, ready))
            await asyncio.wait_for(started.wait(), 2)
            self.assertFalse(ready.is_set(), 'Commands must be gated during GCS restore')
            self.assertFalse((self.root / 'AGENT.md').exists())
            allow_download.set()
            await asyncio.wait_for(restore, 2)

        self.assertTrue(ready.is_set())
        self.assertEqual((self.root / 'AGENT.md').read_text(), 'Project-specific instructions')
        await self.bucket.close(final_sync=False)

    async def test_failed_restore_never_marks_cloud_consumer_ready(self):
        self.bucket.objects['AGENT.md'] = ('101', b'instructions', 0o644)
        ready = asyncio.Event()
        with patch.object(self.bucket, '_download', side_effect=OSError('GCS unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Workspace restore failed'):
                await restore_workspace_before_events(self.bucket, ready)
        self.assertFalse(ready.is_set())
        self.assertIsNone(self.bucket.task)

    async def test_no_gcs_workspace_does_not_block_local_sse(self):
        ready = asyncio.Event()
        await restore_workspace_before_events(None, ready)
        self.assertTrue(ready.is_set())

    async def test_second_connection_does_not_rerun_initial_restore(self):
        ready = asyncio.Event()
        self.bucket.objects['AGENT.md'] = ('101', b'instructions', 0o644)
        await restore_workspace_before_events(self.bucket, ready)
        self.assertTrue(ready.is_set())
        with patch.object(self.bucket, 'start', side_effect=AssertionError('second restore')):
            await restore_workspace_before_events(self.bucket, ready)
        await self.bucket.close(final_sync=False)


if __name__ == '__main__':
    unittest.main()
