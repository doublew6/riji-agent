"""Tests for the Feishu-to-riji-agent Hermes bridge.

These use httpx.MockTransport to assert the exact request shape (header + body)
the bridge constructs, and that it forwards riji-agent's reply text back. No
server is started.
"""

from __future__ import annotations

import json

import httpx
import pytest

from riji_agent.integrations.hermes_bridge import (
    DEFAULT_RIJI_AGENT_URL,
    BridgeConfigError,
    FeishuMessageEvent,
    HermesFeishuBridge,
)

SECRET = "top-secret-shared"


def _event(
    text: str = "你好",
    event_id: str = "evt_1",
    user: str = "ou_1",
    chat_type: str = "p2p",
) -> FeishuMessageEvent:
    return FeishuMessageEvent(
        event_id=event_id,
        feishu_user_id=user,
        chat_id="oc_1",
        chat_type=chat_type,
        text=text,
    )


def _bridge(handler, *, secret: str = SECRET, url: str = DEFAULT_RIJI_AGENT_URL):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return HermesFeishuBridge(riji_agent_url=url, shared_secret=secret, client=client)


def test_forwards_event_fields_and_secret_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["secret"] = request.headers.get("X-Hermes-Secret")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "ok"})

    bridge = _bridge(handler)
    bridge.forward(_event(text="/导师 王阳明"))

    assert captured["url"] == DEFAULT_RIJI_AGENT_URL
    assert captured["secret"] == SECRET
    # Exactly the five contract fields, forwarded verbatim (text not rewritten).
    assert captured["body"] == {
        "event_id": "evt_1",
        "feishu_user_id": "ou_1",
        "chat_id": "oc_1",
        "chat_type": "p2p",
        "text": "/导师 王阳明",
    }


def test_returns_reply_text_from_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "r1",
                "persona_id": "gentle_reviewer",
                "reply": "[gentle_reviewer] 你好",
                "deduplicated": False,
            },
        )

    bridge = _bridge(handler)
    assert bridge.forward(_event()) == "[gentle_reviewer] 你好"


def test_passes_feishu_event_id_without_generating_one() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "ok"})

    bridge = _bridge(handler)
    bridge.forward(_event(event_id="feishu_native_id"))
    # The bridge must transmit Feishu's own id so riji-agent's dedupe works.
    assert captured["body"]["event_id"] == "feishu_native_id"


def test_group_chat_is_forwarded_not_self_authorized() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        # riji-agent would answer 403; bridge must not pre-filter group chats.
        return httpx.Response(403, json={"error": "group_chat_denied"})

    bridge = _bridge(handler)
    reply = bridge.forward(_event(chat_type="group"))
    # chat_type is passed through verbatim; the 403 becomes a safe message.
    assert captured["body"]["chat_type"] == "group"
    assert "无法" in reply


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "unauthenticated"}),
        httpx.Response(403, json={"error": "forbidden_user"}),
        httpx.Response(500, text="boom"),
    ],
)
def test_non_2xx_returns_safe_message_without_secret(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    bridge = _bridge(handler)
    reply = bridge.forward(_event())
    assert SECRET not in reply
    assert reply  # non-empty, user-facing


def test_transport_error_returns_safe_message_without_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    bridge = _bridge(handler)
    reply = bridge.forward(_event())
    assert SECRET not in reply
    assert reply


def test_timeout_returns_specific_safe_message_without_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    bridge = _bridge(handler)
    reply = bridge.forward(_event())
    assert SECRET not in reply
    assert "超时" in reply


def test_malformed_json_returns_safe_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    bridge = _bridge(handler)
    assert bridge.forward(_event())  # falls back to safe reply


def test_empty_reply_falls_back_to_safe_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reply": ""})

    bridge = _bridge(handler)
    assert bridge.forward(_event())


def test_from_mapping_extracts_contract_fields() -> None:
    event = FeishuMessageEvent.from_mapping(
        {
            "event_id": "e9",
            "feishu_user_id": "ou_9",
            "chat_id": "oc_9",
            "chat_type": "p2p",
            "text": "hi",
            "extra_ignored": "whatever",
        }
    )
    assert event == FeishuMessageEvent("e9", "ou_9", "oc_9", "p2p", "hi")


def test_from_env_requires_secret() -> None:
    with pytest.raises(BridgeConfigError):
        HermesFeishuBridge.from_env({})


def test_from_env_defaults_url_and_uses_secret() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["secret"] = request.headers.get("X-Hermes-Secret")
        return httpx.Response(200, json={"reply": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bridge = HermesFeishuBridge.from_env({"HERMES_SHARED_SECRET": "s3cret"}, client=client)
    bridge.forward(_event())
    assert captured["url"] == DEFAULT_RIJI_AGENT_URL
    assert captured["secret"] == "s3cret"


def test_from_env_accepts_timeout() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"reply": "ok"})))
    bridge = HermesFeishuBridge.from_env(
        {"HERMES_SHARED_SECRET": "s3cret", "RIJI_AGENT_TIMEOUT_SECONDS": "123"},
        client=client,
    )
    assert bridge.forward(_event()) == "ok"


def test_construction_rejects_empty_secret() -> None:
    with pytest.raises(BridgeConfigError):
        HermesFeishuBridge(shared_secret="")
