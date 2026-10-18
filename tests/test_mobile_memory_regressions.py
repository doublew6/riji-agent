"""Synthetic factory, recall, consent and overlapping memory-writer regressions."""

import re
import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.config import Settings
from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.journal_engine import _Write
from riji_agent.memory.journal_privacy import PURPOSES
from riji_agent.memory.journal_transfer import JournalMemoryTransfer
from riji_agent.memory.journal_types import JournalCandidate
from riji_agent.memory.models import MemoryScope, MemoryStatus
from riji_agent.memory.review import build_memory_review_router
from riji_agent.mobile.api import build_router
from riji_agent.mobile.memory import MobileMemory, memory_version
from riji_agent.mobile.service import MobileService
from riji_agent.mobile.store import MobileError, MobileStore
from riji_agent.wiring import build_memory_runtime
from test_journal_memory import JournalModel, write_note
from test_mem0_long_term_memory import FakeBackend, _record
from test_mentor_runtime import runtime as mentor_runtime


OWNER = "synthetic-owner"


class ExportBackend(FakeBackend):
    def export_memories(self, *, user_id):
        return tuple(item for item in self.records.values() if item.user_id == user_id)


@pytest.fixture
def factory(tmp_path):
    built = []

    def create(*, journal=False, records=None, mobile=True):
        root = tmp_path / "vault"
        root.mkdir(exist_ok=True)
        settings = Settings(_env_file=None, RIJI_JOURNAL_ROOT=root,
            RIJI_DATA_DIR=tmp_path / f"data-{len(built)}", DEEPSEEK_API_KEY="test-model",
            HERMES_SHARED_SECRET="test-transport", RIJI_ALLOWED_FEISHU_USER_IDS=OWNER,
            RIJI_MEMORY_PROVIDER="mem0", RIJI_MEM0_API_KEY="test-memory",
            RIJI_MOBILE_ENABLED=mobile, RIJI_JOURNAL_MEMORY_ENABLED=journal,
            RIJI_JOURNAL_MEMORY_USER_ID=OWNER, RIJI_MEMORY_SNAPSHOT_ENABLED=False,
            RIJI_MEMORY_REVIEW_ENABLED=True, RIJI_MEMORY_REVIEW_TOKEN="test-review-token")
        raw = ExportBackend(records if records is not None else [_record("m1", "Synthetic fact", user_id=OWNER)])
        model = JournalModel()
        service, worker = build_memory_runtime(settings, backend=raw, extractor_provider=model)
        built.append((service, worker))
        return service, raw, model, settings

    yield create
    for service, worker in built:
        service.operations.close()
        worker._processor._operations.close()
        if service.journal is not None:
            service.journal.store.close()


def review_client(service, settings):
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    assert client.post("/admin/memory/login", json={"token": "test-review-token"}).status_code == 200
    page = client.get("/admin/memory")
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)[1]
    client.headers["X-CSRF-Token"] = token
    return client


@pytest.mark.parametrize("journal", [False, True])
def test_production_factory_mobile_memory_correction(factory, mentor_runtime, tmp_path, journal):
    memory, raw, model, _ = factory(journal=journal)
    runtime, _, _ = mentor_runtime
    mobile = MobileService(MobileStore(tmp_path / "device.sqlite3"), runtime, memory)
    app = FastAPI()
    app.include_router(build_router(mobile))
    client = TestClient(app)
    code = mobile.store.pairing(next(iter(runtime.user_tokens.values())))
    paired = client.post("/mobile/v1/pair", json={"code": code, "device_name": "Synthetic phone"}).json()
    client.headers["Authorization"] = "Bearer " + paired["device_token"]
    before = client.get("/mobile/v1/memories/m1").json()
    response = client.post("/mobile/v1/memories/m1/actions", json={
        "operation_id": "factory-correction", "expected_version": before["version"],
        "action": "update", "content": "Corrected synthetic fact"})
    assert response.status_code == 200 and response.json()["state"] == "completed"
    after = client.get("/mobile/v1/memories/m1").json()
    assert after["content"] == raw.get("m1").content == "Corrected synthetic fact"
    assert after["version"] != before["version"]
    assert client.get("/mobile/v1/operations/factory-correction").json() == response.json()
    assert not model.calls


@pytest.mark.parametrize("journal", [False, True])
@pytest.mark.parametrize("scope", [MemoryScope.SHARED, MemoryScope.PERSONA])
def test_phone_permission_blocks_all_recall_with_or_without_journal(factory, journal, scope):
    item = _record("m1", "Restricted synthetic fact", user_id=OWNER, scope=scope,
                   persona_id="gentle_reviewer" if scope is MemoryScope.PERSONA else None)
    memory, _, _, _ = factory(journal=journal, records=[item])
    mobile = MobileMemory(memory)

    def recalled():
        context = memory.retrieve("fact", user_id=OWNER, persona_id="gentle_reviewer")
        return (*context.shared, *context.persona)

    assert [item.id for item in recalled()] == ["m1"]  # Legacy missing privacy means cloud.
    for permission in ("local", "none", "cloud"):
        mobile.mutate(OWNER, "m1", {"action": "permission", "permission": permission,
            "expected_version": memory_version(memory.backend.get("m1"))})
        assert bool(recalled()) is (permission == "cloud")
        assert mobile.item(OWNER, "m1").content == item.content


@pytest.mark.parametrize("journal", [False, True])
def test_owner_pagination_exports_every_record(factory, journal):
    records = [_record(f"m{index:04}", f"Synthetic fact {index}", user_id=OWNER,
                      status=MemoryStatus.ARCHIVED if index % 2 else MemoryStatus.ACTIVE)
               for index in range(1207)]
    records.append(_record("other", "Other owner's fact", user_id="other-owner"))
    memory, _, _, _ = factory(journal=journal, records=records)
    mobile, cursor, seen = MobileMemory(memory), "", []
    while True:
        page = mobile.listing(OWNER, cursor)
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 1207
    assert set(seen) == {item.id for item in records if item.user_id == OWNER}


def test_non_exporting_backend_never_reports_a_truncated_end(factory, monkeypatch):
    records = [_record(str(index), "Synthetic fact", user_id=OWNER) for index in range(1001)]
    memory, raw, _, _ = factory(records=records)
    monkeypatch.setattr(raw, "export_memories", None)
    with pytest.raises(MemoryBackendError, match="incomplete_memory_listing"):
        MobileMemory(memory).listing(OWNER)


@pytest.mark.parametrize("entrypoint", ["mobile", "review"])
def test_consent_callback_cannot_start_queued_extraction_before_pause(factory, entrypoint):
    memory, raw, model, settings = factory(journal=True, records=[])
    engine = memory.journal
    write_note(engine.policy.root)
    engine.scan()
    assert engine.store.progress()["pending"]
    client = review_client(memory, settings) if entrypoint == "review" else None
    observed = []

    def changed():
        observed.append((engine.privacy.status()["valid"], engine.store.get_control("paused")))
        assert not engine.process_next()

    engine.on_change = changed
    body = {"binding": engine.privacy.binding, "permissions": dict.fromkeys(PURPOSES, True),
            "acknowledged": True, "action": "grant"}
    if client:
        assert client.post("/admin/memory/api/journal/authorize", json=dict(body, user_id=OWNER)).status_code == 200
    else:
        assert MobileMemory(memory).control(OWNER, body) == {"applied": True}
    assert observed == [(True, "1")]
    assert not model.calls and not raw.records
    assert engine.store.rows("SELECT * FROM egress_attempts") == []
    MobileMemory(memory).control(OWNER, {"binding": engine.privacy.binding, "action": "resume"})
    assert engine.process_next()
    assert model.calls and len(raw.records) == 1


def overlap_after_read(monkeypatch, raw, guarded, first, second):
    """Hold the first actual read open while the second reaches the shared guard."""
    read, release, attempted, completed = (threading.Event() for _ in range(4))
    original_get, original_guard = raw.get, guarded.mutation_guard
    results = {}

    def get(identifier):
        item = original_get(identifier)
        if threading.current_thread().name == "first-writer" and identifier == "m1" and not read.is_set():
            read.set()
            assert release.wait(5), "Test did not release the first writer"
        return item

    @contextmanager
    def guard():
        if threading.current_thread().name == "second-writer":
            attempted.set()
        with original_guard():
            yield

    def call(name, operation):
        try:
            results[name] = operation()
        except Exception as exc:
            results[name] = exc
        finally:
            if name == "second":
                completed.set()

    monkeypatch.setattr(raw, "get", get)
    monkeypatch.setattr(guarded, "mutation_guard", guard)
    first_thread = threading.Thread(target=call, args=("first", first), name="first-writer")
    second_thread = threading.Thread(target=call, args=("second", second), name="second-writer")
    first_thread.start()
    try:
        assert read.wait(5), "First writer never read the target"
        second_thread.start()
        assert attempted.wait(5), "Competing writer never reached the guard"
        assert not completed.wait(0.1), "Competing write escaped the read/modify/write guard"
    finally:
        release.set()
        first_thread.join(5)
        if second_thread.ident is not None:
            second_thread.join(5)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    return results


@pytest.mark.parametrize("journal", [False, True])
@pytest.mark.parametrize("action", ["update", "reconfirm", "archive", "restore"])
@pytest.mark.parametrize("first", ["review", "phone"])
def test_review_and_phone_overlap_preserve_permission(factory, monkeypatch, journal, action, first):
    memory, raw, _, _ = factory(journal=journal)
    mobile = MobileMemory(memory)
    expected = memory_version(mobile.item(OWNER, "m1"))

    def review():
        options = {"content": "Reviewed synthetic fact"} if action == "update" else {}
        getattr(memory, action + "_memory")("m1", user_id=OWNER, **options)

    def phone():
        return mobile.mutate(OWNER, "m1", {"action": "permission", "permission": "none",
                                           "expected_version": expected})

    guard = memory.backend.raw if journal else memory.backend
    calls = {"review": review, "phone": phone}
    results = overlap_after_read(monkeypatch, raw, guard, calls[first], calls["phone" if first == "review" else "review"])
    assert not isinstance(results["first"], Exception)
    if first == "review":
        assert isinstance(results["second"], MobileError)
        assert results["second"].code == "memory_version_changed"
        mobile.mutate(OWNER, "m1", {"action": "permission", "permission": "none",
                                   "expected_version": memory_version(mobile.item(OWNER, "m1"))})
    else:
        assert not isinstance(results["second"], Exception)
    assert raw.get("m1").metadata["privacy"] == "none"


@pytest.mark.parametrize("writer", ["cleanup", "operation-repair", "restore-links"])
def test_journal_metadata_writers_cannot_restore_revoked_privacy(factory, monkeypatch, writer):
    memory, raw, _, _ = factory(journal=True)
    engine, mobile = memory.journal, MobileMemory(memory)
    raw.records["m1"] = replace(raw.get("m1"), metadata={"privacy": "cloud", "relation_target": "m2"})
    raw.records["m2"] = _record("m2", "Related synthetic fact", user_id=OWNER)
    engine.store.link("m1", "m2", "related")
    expected = memory_version(mobile.item(OWNER, "m1"))
    if writer == "cleanup":
        background = lambda: engine.erase_backend_memory("m2")
    elif writer == "restore-links":
        transfer = JournalMemoryTransfer(memory.backend)
        original = raw.get("m1")
        background = lambda: transfer._restore_links([original], {"m1": "m1"})
    else:
        write_note(engine.policy.root)
        engine.scan()
        evidence = engine.current_evidence(engine.store.rows("SELECT id FROM evidence")[0]["id"])
        candidate = JournalCandidate("Synthetic fact", "goal", "explicit", evidence.observed_at, ("Synthetic",))
        write = _Write("repair", candidate, evidence, {"action": "new", "reason": "Synthetic repair"}, None)
        monkeypatch.setattr(engine, "_find_operation", lambda write: engine.backend.get("m1"))
        background = lambda: engine._apply(write, lambda: None)
    phone = lambda: mobile.mutate(OWNER, "m1", {"action": "permission", "permission": "none",
                                               "expected_version": expected})
    results = overlap_after_read(monkeypatch, raw, memory.backend.raw, phone, background)
    assert not any(isinstance(result, Exception) for result in results.values()), results
    assert raw.get("m1").metadata["privacy"] == "none"


def test_disabled_mobile_keeps_legacy_review_mutations_available(factory):
    memory, raw, _, _ = factory(mobile=False, journal=True)
    memory.reconfirm_memory("m1", user_id=OWNER)
    memory.update_memory("m1", user_id=OWNER, content="Legacy correction")
    memory.archive_memory("m1", user_id=OWNER)
    memory.restore_memory("m1", user_id=OWNER)
    assert raw.get("m1").content == "Legacy correction"
    assert raw.get("m1").status is MemoryStatus.ACTIVE
    with pytest.raises(MobileError, match="memory_mutation_guard_unavailable"):
        MobileMemory(memory).mutate(OWNER, "m1", {"action": "reconfirm"})
