import json
from typing import Dict, Any, Optional, Literal

from awfl.response_handler import handle_response, process_event

Mode = Literal["execute", "log", "both"]


def event_session_id(obj: Dict[str, Any]) -> Optional[str]:
    try:
        attrs = obj.get("attributes") or {}
        sid = attrs.get("sessionId") if isinstance(attrs, dict) else None
        if sid:
            return str(sid)
        payload = obj.get("payload") or {}
        sid = payload.get("sessionId") if isinstance(payload, dict) else None
        if sid:
            return str(sid)
        cb = obj.get("callback_session") or obj.get("callbackSession")
        if cb:
            return str(cb)
    except Exception:
        pass
    return None


async def forward_event(obj: Dict[str, Any], mode: Mode = "both") -> None:
    """Route an incoming event to logging, execution, or both.

    - mode="execute": perform side effects only (no logs)
    - mode="log": log/status only (no side effects)
    - mode="both": do both in order: log first, then execute
    """
    # Defensive parse of JSON in case upstream occasionally ships a string
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            obj = {"content": str(obj)}

    if mode in ("log", "both"):
        try:
            process_event(obj)
        except Exception:
            # Swallow logger errors to not break execution path
            pass

    if mode in ("execute", "both"):
        await handle_response(obj)
