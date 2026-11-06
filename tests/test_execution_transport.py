import asyncio
import json
import os
import sys
import tempfile
import unittest
from aiohttp import web


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_awfl_run_sse_dispatch_callback(self):
        result = asyncio.get_running_loop().create_future()
        token = 'test-workspace-capability'
        async def stream(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + token)
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            envelope = {'callback_id': '1', 'tool_call': {'function': {'name': 'RUN_COMMAND', 'arguments': {'command': 'echo transport-ok; exit 9'}}}}
            await response.write(('id: 1\ndata: ' + json.dumps(envelope) + '\n\n').encode())
            await asyncio.shield(result)
            return response
        async def callback(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + token)
            if not result.done(): result.set_result(await request.json())
            return web.json_response({'ok': True})
        app = web.Application()
        app.router.add_get('/stream', stream)
        app.router.add_post('/results/1', callback)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        env = {**os.environ, 'AWFL_EXECUTION_URL': f'http://127.0.0.1:{port}', 'AWFL_EXECUTION_TOKEN': token,
               'AWFL_EXECUTION_EXPIRES_AT': '9999999999999'}
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'awfl.main', 'run', env=env,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            done, _ = await asyncio.wait(
                {result, asyncio.create_task(process.wait())},
                timeout=30,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if result not in done:
                if process.returncode is None:
                    process.terminate()
                stdout, stderr = await process.communicate()
                self.fail(
                    f'AWFL consumer did not callback; exit={process.returncode}, '
                    f'stdout={stdout.decode(errors="replace")[-3000:]}, '
                    f'stderr={stderr.decode(errors="replace")[-3000:]}'
                )
            payload = result.result()
            self.assertEqual(payload['exit_code'], 9)
            self.assertIn('transport-ok', str(payload['chunks']))
        finally:
            if process.returncode is None: process.terminate()
            await process.communicate()
            await runner.cleanup()
