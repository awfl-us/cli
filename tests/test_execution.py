import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from awfl.execution.runtime import Runtime
from awfl.consumer.routing import forward_event


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Runtime(self.directory.name, output_limit=65536)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    async def test_existing_tool_envelope_dispatch(self):
        received = []
        async def callback(payload):
            received.append(payload)
        await forward_event({'tool_call': {'function': {'name': 'RUN_COMMAND', 'arguments': {'command': 'echo hello; echo err >&2; exit 7'}}}},
                            mode='execute', runtime=self.runtime, callback=callback)
        self.assertEqual(received[0]['exit_code'], 7)
        self.assertEqual({c['stream'] for c in received[0]['chunks']}, {'stdout', 'stderr'})

    async def test_poll_cancel_and_reuse(self):
        result = await self.runtime.invoke('run_command', {'command': 'echo first; sleep 30; echo last'})
        self.assertEqual(result['state'], 'running')
        self.assertIn('first', str(result['chunks']))
        polled = await self.runtime.invoke('command_output', {'execution_id': result['execution_id'], 'cursor': result['next_cursor']})
        self.assertEqual(polled['chunks'], [])
        cancelled = await self.runtime.invoke('command_cancel', {'execution_id': result['execution_id']})
        self.assertEqual(cancelled['reason'], 'cancelled')
        await self.runtime.invoke('write_file', {'path': 'sub/file', 'content': 'persisted'})
        reused = await self.runtime.invoke('run_command', {'command': 'cat file', 'cwd': 'sub'})
        self.assertIn('persisted', str(reused['chunks']))
        self.assertEqual((await self.runtime.invoke('environment_status', {}))['cwd'], str(Path(self.directory.name) / 'sub'))

    async def test_paths_and_ranges(self):
        for path in ['../escape', '/etc/passwd']:
            with self.assertRaises(ValueError):
                await self.runtime.invoke('read_file', {'path': path})
        (Path(self.directory.name) / 'link').symlink_to('/etc')
        with self.assertRaises(ValueError):
            await self.runtime.invoke('write_file', {'path': 'link/bad', 'content': 'bad'})
        await self.runtime.invoke('write_file', {'path': 'file', 'content': 'abcdef'})
        read = await self.runtime.invoke('read_file', {'path': 'file', 'offset': 2, 'max_bytes': 2})
        self.assertEqual(read, {'content': 'cd', 'next_offset': 4, 'truncated': True})

    async def test_output_limit_and_timeout(self):
        result = await self.runtime.invoke('run_command', {'command': 'yes flooding'})
        await self.runtime.executions[result['execution_id']]['task']
        result = self.runtime.output(result['execution_id'], 0)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['reason'], 'output_limit')
        result = await self.runtime.invoke('run_command', {'command': 'sleep 30', 'timeout': 1})
        await self.runtime.executions[result['execution_id']]['task']
        self.assertEqual(self.runtime.output(result['execution_id'], 0)['reason'], 'timeout')

    async def test_concurrency_and_credential_forwarding(self):
        os.environ['AWFL_EXECUTION_TOKEN'] = 'sensitive-test-token'
        os.environ['GITHUB_TOKEN'] = 'test-github-credential'
        os.environ['FIREBASE_ID_TOKEN'] = 'test-firebase-credential'
        try:
            result = await self.runtime.invoke('run_command', {
                'command': 'printf "%s|%s|%s" "${AWFL_EXECUTION_TOKEN-unset}" "$GITHUB_TOKEN" "$FIREBASE_ID_TOKEN"'
            })
            joined = str(result['chunks'])
            self.assertIn('unset|test-github-credential|test-firebase-credential', joined)
        finally:
            del os.environ['AWFL_EXECUTION_TOKEN']
            del os.environ['GITHUB_TOKEN']
            del os.environ['FIREBASE_ID_TOKEN']
        for _ in range(4):
            await self.runtime.invoke('run_command', {'command': 'sleep 30'})
        with self.assertRaises(ValueError):
            await self.runtime.invoke('run_command', {'command': 'true'})

if __name__ == '__main__':
    unittest.main()
