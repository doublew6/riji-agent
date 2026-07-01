from pathlib import Path
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.hermes.api import build_hermes_router
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.voice.models import VoiceAttachment

SECRET = "top-secret-shared"


class FakeResponder:
    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        return f"[{context.persona_id}] {question}"


class FakeVoiceReplyService:
    def __init__(self, attachment: Optional[VoiceAttachment] = None, fail: bool = False) -> None:
        self.attachment = attachment
        self.fail = fail
        self.calls: list[tuple[str, str, Optional[str]]] = []

    def synthesize_reply(
        self, *, text: str, request_id: str, voice: Optional[str] = None
    ) -> Optional[VoiceAttachment]:
        self.calls.append((text, request_id, voice))
        if self.fail:
            raise RuntimeError("tts failed")
        return self.attachment


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "mem.sqlite3"),
        events=EventLog(tmp_path / "events.sqlite3"),
        responder=FakeResponder(),
    )
    app = FastAPI()
    app.include_router(build_hermes_router(gateway))
    return TestClient(app)


def _client_with_voice(tmp_path: Path, voice_service: FakeVoiceReplyService) -> TestClient:
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "mem.sqlite3"),
        events=EventLog(tmp_path / "events.sqlite3"),
        responder=FakeResponder(),
        voice_reply_service=voice_service,
    )
    app = FastAPI()
    app.include_router(build_hermes_router(gateway))
    return TestClient(app)


def _body(text: str, event_id: str = "e1", chat_type: str = "p2p", user: str = "ou_1") -> dict:
    return {
        "event_id": event_id,
        "feishu_user_id": user,
        "chat_id": "c1",
        "chat_type": chat_type,
        "text": text,
    }


def test_authorized_message_returns_reply(client: TestClient) -> None:
    resp = client.post("/hermes/messages", json=_body("你好"), headers={"X-Hermes-Secret": SECRET})
    assert resp.status_code == 200
    data = resp.json()
    assert data["persona_id"] == "gentle_reviewer"
    assert "你好" in data["reply"]
    assert "audio" not in data


def test_voice_reply_metadata_is_returned_when_enabled(tmp_path: Path) -> None:
    voice = FakeVoiceReplyService(
        VoiceAttachment(path="/tmp/riji-agent-voice/reply.opus", mime_type="audio/ogg")
    )
    client = _client_with_voice(tmp_path, voice)

    resp = client.post("/hermes/messages", json=_body("你好"), headers={"X-Hermes-Secret": SECRET})

    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "[gentle_reviewer] 你好"
    assert data["audio"] == {
        "path": "/tmp/riji-agent-voice/reply.opus",
        "mime_type": "audio/ogg",
    }
    assert voice.calls == [
        ("[gentle_reviewer] 你好", data["request_id"], "Flo (中文（中国大陆）)")
    ]


def test_voice_failure_falls_back_to_text_reply(tmp_path: Path) -> None:
    client = _client_with_voice(tmp_path, FakeVoiceReplyService(fail=True))

    resp = client.post("/hermes/messages", json=_body("你好"), headers={"X-Hermes-Secret": SECRET})

    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "[gentle_reviewer] 你好"
    assert "audio" not in data


def test_missing_secret_is_401(client: TestClient) -> None:
    resp = client.post("/hermes/messages", json=_body("你好"))
    assert resp.status_code == 401


def test_group_chat_is_403(client: TestClient) -> None:
    resp = client.post(
        "/hermes/messages", json=_body("你好", chat_type="group"), headers={"X-Hermes-Secret": SECRET}
    )
    assert resp.status_code == 403


def test_duplicate_event_returns_dedup_flag(client: TestClient) -> None:
    headers = {"X-Hermes-Secret": SECRET}
    client.post("/hermes/messages", json=_body("一次", event_id="dup"), headers=headers)
    resp = client.post("/hermes/messages", json=_body("一次", event_id="dup"), headers=headers)
    assert resp.json()["deduplicated"] is True
