from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from riji_agent.memory.capture import ExtractedMemories
from riji_agent.memory.models import MemoryScope, MemoryStatus
from riji_agent.memory.organization import MemoryOrganizer, _parse_report, memory_version, overdue_ids
from riji_agent.memory.service import CaptureProcessor
from riji_agent.memory.worker import MemoryWorker
from riji_agent.models.types import AssistantTurn
from test_mem0_long_term_memory import (
    FakeBackend, _authenticated_review, _record, _service,
)


class OrganizingProvider:
    def __init__(self) -> None:
        self.batches = []

    def complete(self, messages, tools):
        batch = json.loads(messages[-1]["content"])
        self.batches.append(batch)
        ids = [item["id"] for item in batch]
        pairs = [{"kind": "conflict", "evidence_ids": ids[:2], "reason": "同一属性有不同陈述，需要核实。"}] if len(ids) > 1 else []
        return AssistantTurn(json.dumps({
            "topics": [{"category": "goals", "title": "阶段性计划", "summary": "计划需要结合记录时间理解。", "evidence_ids": ids}],
            "comparisons": pairs, "time_bound_ids": ids[:1],
        }))


def _organize(service, backend, provider=None):
    store = service.operations.organization
    store.request("u1")
    organizer = MemoryOrganizer(backend, store, provider or OrganizingProvider())
    assert organizer.process_next()
    return store.latest("u1", ready_only=True)["report"]


def test_organization_is_scoped_bounded_and_never_mutates_memories(tmp_path: Path) -> None:
    records = [_record(f"m{i}", "fact " + str(i)) for i in range(23)]
    records += [_record("private1", "private one", scope=MemoryScope.PERSONA, persona_id="coach"),
                _record("private2", "private two", scope=MemoryScope.PERSONA, persona_id="friend"),
                _record("other", "another user", user_id="u2"),
                _record("archived", "old", status=MemoryStatus.ARCHIVED)]
    backend = FakeBackend(records)
    service, operations, _ = _service(tmp_path, backend)
    provider = OrganizingProvider()
    report = _organize(service, backend, provider)
    assert report["total"] == report["processed"] == 25
    assert len(provider.batches) == 4
    assert all(len(batch) <= 20 and sum(len(item["content"]) for item in batch) <= 6000 for batch in provider.batches)
    for batch in provider.batches:
        scopes = {(backend.get(item["id"]).scope, backend.get(item["id"]).persona_id) for item in batch}
        assert len(scopes) == 1
        assert not {"other", "archived"}.intersection(item["id"] for item in batch)
    assert list(backend.records.values()) == records
    operations.close()


@pytest.mark.parametrize("mutation", ["unknown_id", "missing_fact", "duplicate_id", "oversize", "cross_scope", "credential"])
def test_invalid_model_organization_is_rejected(mutation: str) -> None:
    payload = {"topics": [{"category": "goals", "title": "目标", "summary": "用户的目标", "evidence_ids": ["a", "b"]}],
               "comparisons": [], "time_bound_ids": []}
    if mutation == "unknown_id":
        payload["time_bound_ids"] = ["another-user-id"]
    elif mutation == "missing_fact":
        payload["topics"][0]["evidence_ids"] = ["a"]
    elif mutation == "duplicate_id":
        payload["topics"][0]["evidence_ids"] = ["a", "a", "b"]
    elif mutation == "oversize":
        payload["topics"][0]["summary"] = "长" * 301
    elif mutation == "cross_scope":
        payload["comparisons"] = [{"kind": "conflict", "evidence_ids": ["a", "private"], "reason": "test"}]
    else:
        payload["topics"][0]["summary"] = "password: unsafe"
    with pytest.raises(ValueError):
        _parse_report(json.dumps(payload), {"a", "b"})


def test_organization_failure_is_retryable_without_touching_memory(tmp_path: Path) -> None:
    backend = FakeBackend((_record("m1", "fact"),))
    service, operations, _ = _service(tmp_path, backend)
    class BrokenProvider:
        def complete(self, messages, tools):
            return AssistantTurn('{"topics": []}')
    store = operations.organization
    first = store.request("u1")
    assert store.request("u1") == first
    assert MemoryOrganizer(backend, store, BrokenProvider()).process_next()
    assert store.latest("u1")["status"] == "failed"
    assert store.latest("u1", ready_only=True) is None
    assert backend.get("m1").content == "fact"
    assert store.request("u1") > first
    assert MemoryOrganizer(backend, store, OrganizingProvider()).process_next()
    assert store.latest("u1")["status"] == "ready"
    operations.close()


def test_request_during_processing_is_not_lost(tmp_path: Path) -> None:
    service, operations, _ = _service(tmp_path, FakeBackend())
    store = operations.organization
    first = store.request("u1")
    assert store.claim()["id"] == first
    second = store.request("u1")
    assert second > first and store.request("u1") == second
    store.finish(first, {"groups": []})
    assert store.latest("u1")["status"] == "pending"
    assert store.claim()["id"] == second
    operations.close()


def test_time_bound_review_demotes_without_deleting_and_reconfirm_resets(tmp_path: Path) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    records = [replace(_record("m1", "an old plan"), metadata={"source_created_at": old}),
               replace(_record("m2", "stable preference"), metadata={"source_created_at": old})]
    backend = FakeBackend(records)
    service, operations, _ = _service(tmp_path, backend)
    report = _organize(service, backend)
    assert overdue_ids(report, records) == {"m1"}
    assert [item.id for item in service.retrieve("query", user_id="u1", persona_id="coach").shared] == ["m2", "m1"]
    assert all(item.status is MemoryStatus.ACTIVE for item in backend.records.values())
    service.reconfirm_memory("m1", user_id="u1")
    assert overdue_ids(report, tuple(backend.records.values())) == set()
    refreshed = _organize(service, backend)
    assert overdue_ids(refreshed, tuple(backend.records.values())) == set()
    assert operations.list_changes(user_id="u1")[0].action == "RECONFIRM"
    operations.close()


def test_missing_observation_date_does_not_invent_expiry(tmp_path: Path) -> None:
    backend = FakeBackend((_record("m1", "old plan"),))
    service, operations, _ = _service(tmp_path, backend)
    report = _organize(service, backend)
    assert not overdue_ids(report, tuple(backend.records.values()))
    operations.close()


def test_capture_records_dedup_decision_and_triggers_organization(tmp_path: Path) -> None:
    backend = FakeBackend((_record("m1", "same fact"),))
    service, operations, _ = _service(tmp_path, backend)
    class Extractor:
        def extract(self, *args, **kwargs):
            return ExtractedMemories(["same  fact", "new fact", "new fact"], [])
    job_id = service.enqueue_capture(source_request_id="capture-1", user_id="u1", persona_id="coach", session_id="chat", content="facts")
    processor = CaptureProcessor(backend, operations, Extractor(), None)
    worker = MemoryWorker(processor, organizer=MemoryOrganizer(backend, operations.organization, OrganizingProvider()))
    assert worker.run_once()
    assert len(backend.records) == 2
    changes = operations.list_changes(user_id="u1")
    assert sum(item.action == "DEDUP_SKIP" for item in changes) == 2
    assert operations.get_job(job_id).status.value == "succeeded"
    assert operations.organization.latest("u1")["status"] == "pending"
    assert worker.run_once()
    assert operations.organization.latest("u1")["status"] == "ready"
    operations.close()


def test_review_organization_auth_evidence_escaping_and_stale_report(tmp_path: Path) -> None:
    backend, service, operations, client, headers = _authenticated_review(tmp_path)
    endpoint = "/admin/memory/api/organize"
    assert client.post(endpoint, json={"user_id": "u1"}).status_code == 403
    assert client.post(endpoint, json={"user_id": "u2"}, headers=headers).status_code == 400
    assert client.post(endpoint, json={"user_id": "u1"}, headers=headers).status_code == 202
    service.update_memory("m1", user_id="u1", content="<script>example</script>")
    report = _organize(service, backend)
    assert report["versions"]["m1"] == memory_version(backend.get("m1"))
    page = client.get("/admin/memory?view=overview")
    assert "阶段性计划" in page.text and "展开 1 条依据" in page.text
    assert "&lt;script&gt;example&lt;/script&gt;" in page.text
    assert "<script>example</script>" not in page.text
    service.archive_memory("m1", user_id="u1")
    page = client.get("/admin/memory?view=overview")
    assert "部分记忆已变化" in page.text
    assert "阶段性计划" not in page.text and "example" not in page.text
    assert "系统不自动删除" in client.get("/admin/memory?view=compare").text
    operations.close()


def test_organization_reports_partial_coverage(tmp_path: Path) -> None:
    backend = FakeBackend([_record(f"m{i:03}", "fact") for i in range(103)])
    service, operations, _ = _service(tmp_path, backend)
    report = _organize(service, backend)
    assert report["processed"] == 100 and report["total"] == 103
    operations.close()
