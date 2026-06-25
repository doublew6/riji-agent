from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.hermes.api import build_hermes_router
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry

SECRET = "top-secret-shared"


class FakeResponder:
    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        return f"[{context.persona_id}] {question}"


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
