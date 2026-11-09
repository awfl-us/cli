"""Connection reuse and retry contract for the existing MCP callback transport."""
import asyncio
import os
import unittest
from unittest.mock import patch

from aiohttp import web

from awfl.response_handler import callbacks


class CallbackSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transports = []
        self.calls = []
        self.statuses = []

        async def handler(request):
            if request.transport not in self.transports:
                self.transports.append(request.transport)
            self.calls.append({
                "authorization": request.headers.get("Authorization"),
                "payload": await request.json(),
            })
            status = self.statuses.pop(0) if self.statuses else 200
            return web.json_response({"ok": status == 200}, status=status)

        self.app = web.Application()
        self.app.router.add_post("/workflows/callbacks/{callback_id}", handler)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.origin = "http://127.0.0.1:" + str(self.site._server.sockets[0].getsockname()[1])

    async def asyncTearDown(self):
        await callbacks.close_callback_session()
        await self.runner.cleanup()

    async def test_sequential_callbacks_reuse_one_tcp_connection(self):
        with patch.object(callbacks, "get_api_origin", return_value=self.origin), \
             patch.object(callbacks, "get_auth_headers", return_value={}):
            for n in range(12):
                self.assertTrue(await callbacks.post_internal_callback("test", {"n": n}))
        self.assertEqual(len(self.calls), 12)
        self.assertEqual(len(self.transports), 1)

    async def test_refreshes_auth_headers_even_when_connection_is_pooled(self):
        credentials = iter([
            {"Authorization": "Bearer first"},
            {"Authorization": "Bearer rotated"},
        ])
        with patch.object(callbacks, "get_api_origin", return_value=self.origin), \
             patch.object(callbacks, "get_auth_headers", side_effect=lambda: next(credentials)):
            self.assertTrue(await callbacks.post_internal_callback("test", {"n": 1}))
            self.assertTrue(await callbacks.post_internal_callback("test", {"n": 2}))
        self.assertEqual([x["authorization"] for x in self.calls],
                         ["Bearer first", "Bearer rotated"])
        self.assertEqual(len(self.transports), 1)

    async def test_retry_503_then_success_on_same_connection(self):
        self.statuses = [503, 200]
        with patch.object(callbacks, "get_api_origin", return_value=self.origin), \
             patch.object(callbacks, "get_auth_headers", return_value={}), \
             patch.dict(os.environ, {"CALLBACK_RETRY_DELAY_MS": "1"}):
            self.assertTrue(await callbacks.post_internal_callback("test", {"n": 1}))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.transports), 1)

    async def test_explicit_shutdown_closes_pool_and_next_call_reconnects(self):
        with patch.object(callbacks, "get_api_origin", return_value=self.origin), \
             patch.object(callbacks, "get_auth_headers", return_value={}):
            self.assertTrue(await callbacks.post_internal_callback("test", {"n": 1}))
            await callbacks.close_callback_session()
            self.assertTrue(await callbacks.post_internal_callback("test", {"n": 2}))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.transports), 2)

    async def test_concurrent_callback_requests(self):
        with patch.object(callbacks, "get_api_origin", return_value=self.origin), \
             patch.object(callbacks, "get_auth_headers", return_value={}):
            done = await asyncio.gather(*(
                callbacks.post_internal_callback("test", {"n": n})
                for n in range(8)
            ))
        self.assertEqual(done, [True] * 8)
        self.assertEqual(len(self.calls), 8)


if __name__ == "__main__":
    unittest.main()
