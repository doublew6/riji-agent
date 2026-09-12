from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_store import JournalMemoryStore
from riji_agent.memory.service import CaptureProcessor
from riji_agent.models.types import LLMError
from test_journal_lifecycle import attached_service
from test_journal_memory import JournalModel, runtime, write_note
from test_mem0_long_term_memory import FakeBackend, FakeExtractor, _record, _service
from test_memory_organization import OrganizingProvider


class GuardedModel:
    def __init__(self, base, waiting=lambda messages: None):
        self.base, self.waiting, self.sent = base, waiting, []

    def complete(self, messages, tools):
        raise AssertionError("must use guarded completion")

    def complete_with_guard(self, messages, tools, *, before_send):
        self.waiting(messages)
        before_send()
        self.sent.append(messages)
        return self.base.complete(messages, tools)


@pytest.mark.parametrize("change", ["revoke", "source_private"])
def test_waiting_extraction_rechecks_permission_before_budget_and_send(tmp_path, change):
    engine, base = runtime(tmp_path)
    path = write_note(engine.policy.root)
    def waiting(messages):
        if change == "revoke":
            engine.privacy.revoke()
        else:
            write_note(engine.policy.root, extra="---\nmemory: local\n---\n")
    provider = GuardedModel(base, waiting)
    engine.provider = provider
    assert engine.process_next()
    assert not provider.sent and not base.calls and not engine.backend.records
    assert engine.store.rows("SELECT * FROM budgets") == []
    assert engine.store.rows("SELECT * FROM egress_attempts") == []
    assert path.is_file()


def test_waiting_relation_rechecks_old_memory_privacy(tmp_path):
    engine, base = runtime(tmp_path)
    write_note(engine.policy.root)
    assert engine.process_next()
    write_note(engine.policy.root, "daily/2026-09-01.md", "准备先学习产品需求分析。")
    engine.scan()
    def waiting(messages):
        if "existing" in json.loads(messages[-1]["content"]):
            item = engine.backend.get("m1")
            engine.backend.update("m1", metadata=dict(item.metadata, privacy="local"))
    provider = GuardedModel(base, waiting)
    engine.provider = provider
    assert engine.process_next()
    assert len(provider.sent) == 1  # Extraction only; the relation payload is withheld.
    assert len(engine.backend.records) == 1
    assert engine.store.rows("SELECT 1 FROM egress_attempts WHERE phase='relation'") == []


@pytest.mark.parametrize("journal", [False, True])
def test_waiting_organization_rechecks_memory_privacy(tmp_path, journal):
    if journal:
        engine, _ = runtime(tmp_path)
        write_note(engine.policy.root)
        assert engine.process_next()
        service = attached_service(tmp_path, engine)
        backend = service.backend
    else:
        backend = FakeBackend((_record("m1", "User prefers concise explanations."),))
        service, _, _ = _service(tmp_path, backend)
    def waiting(messages):
        item = backend.get("m1")
        backend.update("m1", metadata=dict(item.metadata, privacy="local"))
    provider = GuardedModel(OrganizingProvider(), waiting)
    store = service.operations.organization
    store.request("u1")
    assert MemoryOrganizer(backend, store, provider).process_next()
    assert not provider.sent
    assert store.latest("u1", ready_only=True) is None
    if journal:
        assert engine.store.rows("SELECT 1 FROM egress_attempts WHERE phase='organization'") == []


@pytest.mark.parametrize("code,delay", [("codex_quota_exhausted", 900), ("codex_login_required", 300)])
def test_codex_journal_unavailability_preserves_job_without_dead_letter_or_budget(tmp_path, code, delay):
    def waiting(messages):
        raise LLMError(code)
    provider = GuardedModel(JournalModel(), waiting)
    engine, _ = runtime(tmp_path, model=provider)
    write_note(engine.policy.root)
    for _ in range(6):
        engine.store.execute("UPDATE evidence SET available_at='1970-01-01'")
        assert engine.process_next()
        job = engine.store.rows("SELECT * FROM evidence")[0]
        assert job["status"] == "retry" and job["attempts"] == 0
        assert job["error"] == code and engine.current_evidence(job["id"]).text
        remaining = datetime.fromisoformat(job["available_at"]) - datetime.now(timezone.utc)
        assert delay - 5 < remaining.total_seconds() <= delay
        assert not engine.process_next()
    assert not provider.sent and not engine.backend.records
    assert engine.store.rows("SELECT * FROM budgets") == []
    assert engine.store.rows("SELECT * FROM egress_attempts") == []
    engine.store.close()
    engine = JournalMemoryEngine(engine.policy, JournalMemoryStore(engine.store.path), engine.backend, provider)
    assert not engine.process_next()  # Persisted backoff survives a service restart.
    provider.waiting = lambda messages: None
    engine.store.execute("UPDATE evidence SET available_at='1970-01-01'")
    assert engine.process_next()
    assert engine.store.rows("SELECT status FROM evidence")[0]["status"] == "succeeded"
    assert len(engine.backend.records) == 1


@pytest.mark.parametrize("code", ["codex_quota_exhausted", "codex_login_required"])
def test_codex_native_capture_unavailability_preserves_payload(tmp_path, code):
    class UnavailableExtractor:
        def extract(self, *args, **kwargs):
            raise LLMError(code)
    backend = FakeBackend()
    service, operations, _ = _service(tmp_path, backend)
    job_id = operations.enqueue(source_request_id="synthetic", user_id="u1", persona_id="coach",
                                session_id="s1", content="I prefer concise answers.")
    processor = CaptureProcessor(backend, operations, UnavailableExtractor(), None)
    for _ in range(6):
        operations._conn.execute("UPDATE memory_capture_jobs SET next_attempt_at='1970-01-01'")
        operations._conn.commit()
        assert processor.process_next()
        job = operations.get_job(job_id)
        assert job.status.value == "retry" and job.attempts == 0
        assert job.error_code == code and job.content == "I prefer concise answers."
        assert not processor.process_next()
    assert not backend.records
    operations._conn.execute("UPDATE memory_capture_jobs SET next_attempt_at='1970-01-01'")
    operations._conn.commit()
    assert CaptureProcessor(backend, operations, FakeExtractor(), None).process_next()
    assert operations.get_job(job_id).status.value == "succeeded"
    assert operations.get_job(job_id).content is None


@pytest.mark.parametrize("journal", [False, True])
@pytest.mark.parametrize("code", ["codex_quota_exhausted", "codex_login_required"])
def test_codex_organization_unavailability_retains_pending_run(tmp_path, journal, code):
    if journal:
        engine, _ = runtime(tmp_path)
        write_note(engine.policy.root)
        assert engine.process_next()
        service = attached_service(tmp_path, engine)
        backend = service.backend
    else:
        backend = FakeBackend((_record("m1", "User prefers concise explanations."),))
        service, _, _ = _service(tmp_path, backend)
    def waiting(messages):
        raise LLMError(code)
    provider = GuardedModel(OrganizingProvider(), waiting)
    store = service.operations.organization
    run_id = store.request("u1")
    organizer = MemoryOrganizer(backend, store, provider)
    assert organizer.process_next()
    run = store.latest("u1")
    assert run["id"] == run_id and run["status"] == "pending" and run["error_code"] == code
    assert not organizer.process_next()
    assert not provider.sent
