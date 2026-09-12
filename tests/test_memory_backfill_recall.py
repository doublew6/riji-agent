from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from riji_agent.agent.tools import ToolRegistry
from riji_agent.memory.backfill import MemoryBackfill
from riji_agent.memory.capture import DeepSeekMemoryExtractor, ExtractedMemories
from riji_agent.memory.models import MemoryScope
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.service import CaptureProcessor
from riji_agent.memory.store import MemoryStore
from riji_agent.models.types import LLMError
from riji_agent.retrieval.models import ToolContext
from test_mem0_long_term_memory import FakeBackend, _service


class RecordingExtractor:
    def __init__(self) -> None:
        self.calls = []

    def extract(self, content, *, persona_id, source_created_at):
        self.calls.append((content, persona_id, source_created_at))
        return ExtractedMemories(("User prefers concise answers.",), ())


def test_backfill_excludes_foreign_assistant_and_secret_sources_and_is_repeatable(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    message = store.append_message("u1", "coach", "c1", "user", "帮我记住我喜欢简短回答")
    store.append_message("u1", "coach", "c1", "assistant", "Invented assistant fact")
    store.append_message("other", "coach", "c1", "user", "Foreign user")
    store.append_message("u1", "coach", "c1", "user", "确认保存")
    store.append_message("u1", "coach", "c1", "user", "password: secret")
    store.append_message("u1", "other", "c1", "user", "记录日记：我计划长期坚持读书")
    backend = FakeBackend()
    service, operations, snapshot = _service(tmp_path, backend)
    backfill = MemoryBackfill(store, operations, {"u1"})
    preview = backfill.run(apply=False)
    assert (preview.discovered, preview.eligible, preview.skipped) == (4, 2, 2)
    assert operations.list_jobs() == ()
    result = backfill.run(apply=True)
    assert result.enqueued == 2 and result.backup_path.is_file()
    assert result.backup_path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(result.backup_path) as db:
        assert db.execute("SELECT COUNT(*) FROM session_messages").fetchone()[0] == 6
    assert backfill.run(apply=True).already_queued == 2
    assert service.enqueue_capture(
        source_request_id="live-request", user_id="u1", persona_id="coach", session_id="u1:coach:c1",
        content=message.content, source_message_id=message.id, source_created_at=message.created_at,
    ) == operations.list_jobs()[-1].id
    extractor = RecordingExtractor()
    processor = CaptureProcessor(backend, operations, extractor, snapshot)
    assert processor.process_next()
    record = next(iter(backend.records.values()))
    assert record.metadata["source_id"] == f"conversation/{message.id}"
    assert record.metadata["source_created_at"] == message.created_at
    assert extractor.calls[0] == (message.content, "coach", message.created_at)
    assert processor.process_next()
    assert len(backend.records) == 1
    assert all(job.content is None and job.extracted_json is None for job in operations.list_jobs())
    assert backfill.run(apply=True).enqueued == 0
    assert "Evidence: conversation/" in snapshot.path.read_text()
    store.close()
    operations.close()


def test_capture_retry_reuses_extraction_and_does_not_duplicate_partial_writes(tmp_path):
    backend = FakeBackend()
    service, operations, snapshot = _service(tmp_path, backend)
    extractor = RecordingExtractor()
    job_id = service.enqueue_capture(
        source_request_id="retry", user_id="u1", persona_id="coach", session_id="u1:coach:c1",
        content="I prefer concise answers.",
    )
    processor = CaptureProcessor(backend, operations, extractor, snapshot)
    original_write = snapshot.write
    snapshot.write = lambda: (_ for _ in ()).throw(OSError("private error"))
    assert processor.process_next()
    assert operations.get_job(job_id).status.value == "retry"
    assert len(backend.records) == 1
    snapshot.write = original_write
    operations.retry(job_id)
    assert processor.process_next()
    assert len(extractor.calls) == 1
    assert len(backend.records) == 1
    assert operations.get_job(job_id).status.value == "succeeded"
    operations.close()


def test_backfill_refuses_to_enqueue_when_backup_fails(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.append_message("u1", "coach", "c1", "user", "A lasting preference")
    operations = MemoryOperationsStore(tmp_path / "operations.sqlite3")
    def fail(_destination):
        raise OSError("backup failed")
    monkeypatch.setattr(store, "backup_to", fail)
    with pytest.raises(OSError):
        MemoryBackfill(store, operations, {"u1"}).run(apply=True)
    assert not operations.list_jobs()
    store.close()
    operations.close()


@pytest.mark.parametrize("payload", ["null", "[]", '{"shared": "wrong", "persona": []}', '{}'])
def test_invalid_extraction_is_failure_not_silent_empty_success(payload):
    with pytest.raises(LLMError):
        DeepSeekMemoryExtractor._parse(payload)


def test_session_recall_is_bounded_chinese_search_and_audits_evidence(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    original = store.append_message("u1", "coach", "c1", "user", "我长期坚持读书。" * 100)
    for _ in range(13):
        store.append_message("u1", "coach", "c1", "user", "later unrelated message")
    for user, persona, chat, role in (
        ("u2", "coach", "c1", "user"), ("u1", "other", "c1", "user"),
        ("u1", "coach", "c2", "user"), ("u1", "coach", "c1", "assistant"),
    ):
        store.append_message(user, persona, chat, role, "读书 PRIVATE CONTENT")
    store.append_message("u1", "coach", "c1", "user", "读书 password: confidential")
    registry = ToolRegistry(None, memory_store=store)
    context = ToolContext("request", "u1:coach:c1", "u1", "coach")
    result = registry.invoke(context, "session_search", json.dumps({"query": "读书"}))
    assert result.ok
    assert result.source_ids == (f"conversation/{original.id}",)
    assert len(result.payload["items"][0]["snippet"]) == 400
    assert result.payload["truncated"]
    assert "PRIVATE" not in json.dumps(result.payload)
    assert "confidential" not in json.dumps(result.payload)
    assert "session_search" in {t["function"]["name"] for t in registry.tool_specs()}
    assert registry.tool_specs(("search_journal",))[0]["function"]["name"] == "search_journal"
    store.close()


@pytest.mark.parametrize("arguments", [
    {"query": "fact", "user_id": "other"}, {"query": "fact", "session_id": "u2:coach:c1"},
    {"query": ""}, {"query": 4}, {"query": "fact", "top_k": -1}, {"query": "fact", "top_k": True},
])
def test_session_recall_rejects_identity_overrides_and_invalid_arguments(tmp_path, arguments):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    registry = ToolRegistry(None, memory_store=store)
    context = ToolContext("request", "u1:coach:c1", "u1", "coach")
    assert not registry.invoke(context, "session_search", json.dumps(arguments)).ok
    forged = ToolContext("request", "u2:coach:c1", "u1", "coach")
    assert not registry.invoke(forged, "session_search", '{"query":"fact"}').ok
    store.close()
