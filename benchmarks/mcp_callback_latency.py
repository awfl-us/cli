"""Reproducible AWFL consumer callback HTTP baseline.

Run inside an existing AWFL GKE consumer with:
    python benchmarks/mcp_callback_latency.py

No production callback records are written. The callback benchmark uses an
in-process aiohttp server, while the connection benchmark GETs the server's
public health endpoint. Output is one JSON object with raw samples.
"""
import asyncio
import importlib
import json
import random
import statistics
import time

import aiohttp
from aiohttp import web

from awfl.utils import get_api_origin


def summarize(samples):
    seq = sorted(round(v, 3) for v in samples)
    return {
        "count": len(seq),
        "median_ms": round(statistics.median(seq), 3),
        "p95_ms": seq[min(len(seq) - 1, int(len(seq) * 0.95))],
        "min_ms": seq[0],
        "max_ms": seq[-1],
        "samples_ms": [round(v, 3) for v in samples],
    }


async def callback_function_benchmark(count=30):
    callbacks = importlib.import_module("awfl.response_handler.callbacks")
    transports = []
    async def callback_handler(request):
        transport = request.transport
        if transport not in transports:
            transports.append(transport)
        await request.json()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/workflows/callbacks/{callback_id}", callback_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    previous_origin = callbacks.get_api_origin
    previous_headers = callbacks.get_auth_headers
    callbacks.get_api_origin = lambda: f"http://127.0.0.1:{port}"
    callbacks.get_auth_headers = lambda: {}
    samples = []
    try:
        for i in range(count):
            now = time.perf_counter()
            ok = await callbacks.post_internal_callback("benchmark", {"iteration": i})
            if not ok:
                raise RuntimeError("Local callback did not return success")
            samples.append((time.perf_counter() - now) * 1000)
        closer = getattr(callbacks, "close_callback_session", None)
        if closer:
            await closer()
    finally:
        callbacks.get_api_origin = previous_origin
        callbacks.get_auth_headers = previous_headers
        await runner.cleanup()
    return {
        "latency": summarize(samples),
        "tcp_connections": len(transports),
        "expected_callback_count": count,
    }


async def health_connection_benchmark(per_style=20):
    url = get_api_origin().rstrip("/") + "/health"
    timeout = aiohttp.ClientTimeout(total=12)
    samples = {"fresh_session": [], "reused_session": []}
    status_codes = {"fresh_session": set(), "reused_session": set()}
    order = ["fresh_session", "reused_session"] * per_style
    random.Random(7123).shuffle(order)
    async with aiohttp.ClientSession(timeout=timeout) as reused:
        for style in order:
            start = time.perf_counter()
            if style == "fresh_session":
                async with aiohttp.ClientSession(timeout=timeout) as temporary:
                    async with temporary.get(url) as response:
                        status_codes[style].add(response.status)
                        await response.read()
            else:
                async with reused.get(url) as response:
                    status_codes[style].add(response.status)
                    await response.read()
            samples[style].append((time.perf_counter() - start) * 1000)
    return {
        "fresh_session": summarize(samples["fresh_session"]),
        "reused_session": summarize(samples["reused_session"]),
        "status_codes": {key: sorted(value) for key, value in status_codes.items()},
    }


async def main():
    result = {
        "benchmark": "awfl-mcp-callback-latency-v1",
        "callback_function": await callback_function_benchmark(),
        "http_to_cloud_run": await health_connection_benchmark(),
    }
    print("AWFL_CALLBACK_BENCHMARK_JSON=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
