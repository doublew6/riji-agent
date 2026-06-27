import json
from typing import Any, Dict, List

import httpx
import pytest

from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.types import LLMError

API_KEY = "sk-super-secret-key"


def _provider(handler) -> DeepSeekProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return DeepSeekProvider(api_key=API_KEY, model="deepseek-reasoner", client=client)


def test_maps_tool_calls_and_sends_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        body = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "search_journal", "arguments": "{\"query\": \"x\"}"}}
                        ],
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    turn = _provider(handler).complete(
        [{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "search_journal"}}]
    )

    assert captured["auth"] == f"Bearer {API_KEY}"
    assert captured["body"]["model"] == "deepseek-reasoner"
    assert "tools" in captured["body"]
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "search_journal"


def test_maps_plain_text_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "答案"}}]})

    turn = _provider(handler).complete([{"role": "user", "content": "hi"}], [])
    assert turn.content == "答案"
    assert turn.tool_calls == ()


def test_omits_tools_field_when_no_tools() -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _provider(handler).complete([{"role": "user", "content": "hi"}], [])
    assert "tools" not in captured["body"]
    assert "tool_choice" not in captured["body"]


def test_http_error_does_not_leak_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LLMError) as err:
        _provider(handler).complete([{"role": "user", "content": "hi"}], [])
    assert API_KEY not in str(err.value)


def test_malformed_response_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(LLMError):
        _provider(handler).complete([{"role": "user", "content": "hi"}], [])
