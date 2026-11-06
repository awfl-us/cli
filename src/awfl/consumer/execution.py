"""Workspace-scoped SSE tool consumer, using AWFL's existing event parser.

No Firebase/user API key is given to this process. Its capability authorizes only
reading this workspace's requests and posting bounded responses to those requests.
"""
import asyncio
import json
import os
import time
import aiohttp
from awfl.consumer.sse_parser import SSEParser
from awfl.execution.runtime import Runtime
from .routing import forward_event
from awfl.response_handler.callbacks import post_internal_callback


async def consume_execution_events():
    base = os.environ['AWFL_EXECUTION_URL'].rstrip('/')
    token = os.environ.pop('AWFL_EXECUTION_TOKEN')
    deadline = float(os.environ['AWFL_EXECUTION_EXPIRES_AT']) / 1000
    runtime = Runtime('/workspace')
    cursor = 0
    # Response cache prevents repeating side effects after an SSE reconnect.
    results = {}
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=45)
    try:
        async with aiohttp.ClientSession(headers={'Authorization': f'Bearer {token}'}, timeout=timeout) as http:
            while time.time() < deadline:
                try:
                    async with http.get(base + '/stream', headers={'Last-Event-ID': str(cursor)}) as response:
                        if response.status in (401, 403, 410):
                            return
                        response.raise_for_status()
                        parser = SSEParser()
                        async for raw in response.content:
                            event = parser.feed_line(raw.decode('utf-8'))
                            if not event or not event['data']:
                                continue
                            request = json.loads(event['data'])
                            seq = int(event['id'])
                            if seq not in results:
                                async def capture(payload):
                                    results[seq] = payload
                                await forward_event(request, mode='execute', runtime=runtime, callback=capture)
                            await post_internal_callback(str(seq), results[seq], execution_url=base,
                                                         execution_token=token)
                            cursor = seq
                            # Server only replays requests newer than cursor.
                            for old in list(results):
                                if old < cursor:
                                    del results[old]
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    await asyncio.sleep(2)
    finally:
        await runtime.close()



