import asyncio
import json
import aiohttp
from google.cloud import pubsub_v1

from utils import log_unique


async def consume_autofunds(subscription_path: str):
    """
    Consume AutofundsAppointment messages and forward to the local handler.
    Maintains original behavior: acknowledge immediately after scheduling the forward.
    """
    subscriber = pubsub_v1.SubscriberClient()
    loop = asyncio.get_running_loop()

    async def forward_request(payload: str):
        try:
            data = json.loads(payload)
            url = "http://localhost:5050/api/calls/autofunds/process-appointment"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as resp:
                    text = await resp.text()
                    log_unique(f"Autofunds forwarded request to {url} (status {resp.status}): {text}")
        except Exception as e:
            log_unique(f"Autofunds error forwarding request: {e}")

    def callback(message: pubsub_v1.subscriber.message.Message):
        payload = message.data.decode("utf-8")
        log_unique(f"Received AutofundsAppointment message: {payload}")
        asyncio.run_coroutine_threadsafe(forward_request(payload), loop)
        message.ack()

    subscriber.subscribe(subscription_path, callback=callback)
    while True:
        await asyncio.sleep(5)
