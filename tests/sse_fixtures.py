"""Synthetic wire fixtures: actual SSE frames, never a JSON fallback."""

from copy import deepcopy
import json
from typing import Any

import httpx


def event(delta: dict[str, Any], finish: str | None = None) -> bytes:
    body = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return ("data: " + json.dumps(body, ensure_ascii=False) + "\n\n").encode()


def completion_bytes(message: dict[str, Any]) -> bytes:
    delta = deepcopy(message)
    for index, call in enumerate(delta.get("tool_calls") or []):
        call["index"] = index
    finish = "tool_calls" if delta.get("tool_calls") else "stop"
    return event(delta) + event({}, finish) + b"data: [DONE]\n\n"


def completion_response(message: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=completion_bytes(message),
                          headers={"Content-Type": "text/event-stream"})
