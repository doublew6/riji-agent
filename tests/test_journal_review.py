from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.memory.review import build_memory_review_router
from riji_agent.wiring import build_memory_runtime
from test_journal_lifecycle import attached_service
from test_journal_memory import JournalModel, runtime, write_note
from test_mem0_long_term_memory import FakeBackend, _settings


def test_review_journal_controls_require_owner_auth_and_csrf(tmp_path: Path) -> None:
    import re
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    service = attached_service(tmp_path, engine)
    settings = _settings(tmp_path)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    url = "/admin/memory/api/journal/pause"
    assert client.post(url, json={"user_id": "u1"}).status_code == 401
    client.post("/admin/memory/login", json={"token": settings.memory_review_token.get_secret_value()})
    page = client.get("/admin/memory?view=sources")
    assert "2026-08-01.md" in page.text and "待处理" in page.text
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)[1]
    headers = {"X-CSRF-Token": csrf}
    assert client.post(url, json={"user_id": "u1"}).status_code == 403
    assert client.post(url, json={"user_id": "u2"}, headers=headers).status_code == 400
    assert client.post(url, json={"user_id": "u1"}, headers=headers).status_code == 200
    assert engine.store.progress()["paused"]
    assert client.post("/admin/memory/api/journal/resume", json={"user_id": "u1"}, headers=headers).status_code == 200
    assert not engine.store.progress()["paused"]
    assert not model.calls


def test_disabled_journal_still_filters_previously_derived_memory(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"journal_memory_enabled": True, "journal_memory_user_id": "u1"})
    raw, provider = FakeBackend(), JournalModel()
    service, worker = build_memory_runtime(settings, backend=raw, extractor_provider=provider)
    write_note(settings.journal_root)
    service.journal.scan()
    service.journal.privacy.grant(service.journal.privacy.binding, dict.fromkeys(("history", "incremental", "organization", "recall"), True))
    assert service.journal.process_next()
    assert service.retrieve("目标", user_id="u1", persona_id="blunt_coach").shared
    disabled = settings.model_copy(update={"journal_memory_enabled": False, "journal_memory_user_id": ""})
    second, _ = build_memory_runtime(disabled, backend=raw, extractor_provider=provider)
    assert second.journal is not None
    assert not second.retrieve("目标", user_id="u1", persona_id="coach").shared
    assert not second.journal.process_next()


def test_review_export_authentication_and_complete_catalog(tmp_path: Path) -> None:
    from riji_agent.memory.models import MemoryScope
    engine, _ = runtime(tmp_path)
    for index in range(1005):
        engine.backend.add(f"Synthetic fact {index}", user_id="u1", scope=MemoryScope.SHARED,
                           persona_id=None, metadata={"source_type": "conversation"})
    engine.backend.export_memories = lambda **kwargs: tuple(engine.backend.records.values())
    service = attached_service(tmp_path, engine)
    assert len(service.list_memories(user_id="u1")) == 1005
    assert service.snapshot.write()[1] == 1005
    settings = _settings(tmp_path)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    url = "/admin/memory/export?user_id=u1"
    assert client.get(url).status_code == 401
    client.post("/admin/memory/login", json={"token": settings.memory_review_token.get_secret_value()})
    assert client.get("/admin/memory/export?user_id=u2").status_code == 400
    response = client.get(url)
    assert response.status_code == 200 and len(response.json()["memories"]) == 1005
    assert response.headers["cache-control"] == "no-store"
