import asyncio
import json
from google.cloud import pubsub_v1

from utils import log_unique, _get_workflow_env_suffix, _strip_env_suffix
from response_handler import handle_response, get_session


async def consume_cli_operations(subscription_path: str):
    """
    Consume CLI operation messages from Pub/Sub and forward them to handle_response.
    - Filters messages by attributes.sessionId matching the current session (response_handler.get_session()),
      except when the message payload has background=true; such messages are processed regardless of the selected session.
      (Backward-compat: messages whose attributes.sessionId starts with 'background-' are also treated as background.)
    - Accepts either exact session match or a match ignoring the configured WORKFLOW_ENV suffix to
      align with how the prompt shows the selected workflow (unsuffixed).
    - Expects each message.data to be a JSON string compatible with handle_response(data).
    - ACKs only after successful processing; NACKs on failure or (non-background) session mismatch for redelivery.
    """
    subscriber = pubsub_v1.SubscriberClient()
    loop = asyncio.get_running_loop()

    async def process_message(data: dict) -> bool:
        try:
            await handle_response(data)
            return True
        except Exception as e:
            log_unique(f"⚠️ CLI operations processing error: {e}")
            return False

    def _session_matches(current_session: str | None, msg_session: str | None, *, is_background: bool) -> bool:
        if is_background:
            return True
        if not current_session or not msg_session:
            return False
        if msg_session == current_session:
            return True
        # Allow match ignoring env suffix (e.g., Dev) to keep UX consistent with the prompt
        suffix = _get_workflow_env_suffix()
        cur_base = _strip_env_suffix(current_session, suffix)
        msg_base = _strip_env_suffix(msg_session, suffix)
        return cur_base == msg_base

    def callback(message: pubsub_v1.subscriber.message.Message):
        # Decode payload first to inspect background flag
        try:
            payload_text = message.data.decode("utf-8")
            data = json.loads(payload_text)
        except Exception as e:
            log_unique(f"⚠️ Failed to decode Pub/Sub message JSON: {e}")
            message.nack()
            return

        # Filter by message attribute 'sessionId' unless background
        msg_attrs = getattr(message, 'attributes', {}) or {}
        msg_session = msg_attrs.get('sessionId')

        current_session = get_session()

        # New behavior: use boolean 'background' in the payload
        is_background = bool(data.get('background') is True)
        # Backward-compat: accept legacy background- prefix
        if not is_background and isinstance(msg_session, str) and msg_session.startswith('background-'):
            is_background = True

        if not _session_matches(current_session, msg_session, is_background=is_background):
            # Not our session; NACK so another subscriber on the same subscription can receive it
            message.nack()
            return

        future = asyncio.run_coroutine_threadsafe(process_message(data), loop)

        def done(fut: asyncio.Future):
            try:
                ok = fut.result()
                if ok:
                    message.ack()
                else:
                    message.nack()
                    log_unique("⚠️ CLI operation failed; NACKed for retry")
            except Exception as e:
                log_unique(f"⚠️ CLI operations callback error: {e}")
                message.nack()

        future.add_done_callback(done)

    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)

    try:
        while True:
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        streaming_pull_future.cancel()
        # Allow cancellation to propagate
        raise