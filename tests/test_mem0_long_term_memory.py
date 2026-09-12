from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.config import Settings
from riji_agent.config_cli import _snapshot_consistency_ok
from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.capture import (
    DeepSeekMemoryExtractor,
    ExtractedMemories,
    should_auto_capture,
)
from riji_agent.memory.mem0 import Mem0Client
from riji_agent.memory.migration import MemoryMigrator
from riji_agent.memory.models import (
    LongTermMemory,
    MemoryHistoryEntry,
    MemoryScope,
    MemoryStatus,
)
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.review import build_memory_review_router
from riji_agent.memory.service import CaptureProcessor, MemoryService
from riji_agent.memory.snapshot import MemorySnapshotWriter
from riji_agent.memory.store import MemoryStore


class FakeBackend:
    def __init__(self, records: Sequence[LongTermMemory] = ()) -> None:
        self.records = {record.id: record for record in records}
        self.available = True
        self.next_id = len(records) + 1

    def health(self) -> bool:
        return self.available

    def search(self, query, *, user_id, scope, persona_id=None, limit=8):
        if not self.available:
            raise MemoryBackendError("mem0_unavailable")
        return tuple(
            item for item in self.records.values()
            if item.user_id == user_id
            and item.scope is scope
            and item.status is MemoryStatus.ACTIVE
            and (scope is MemoryScope.SHARED or item.persona_id == persona_id)
        )[:limit]

    def list_memories(
        self, *, user_id, persona_id=None, include_archived=False, limit=1000
    ):
        if not self.available:
            raise MemoryBackendError("mem0_unavailable")
        return tuple(
            item for item in self.records.values()
            if item.user_id == user_id
            and (persona_id is None or item.persona_id == persona_id)
            and (include_archived or item.status is MemoryStatus.ACTIVE)
        )[:limit]

    def get(self, memory_id: str) -> LongTermMemory:
        try:
            return self.records[memory_id]
        except KeyError:
            raise MemoryBackendError("memory_not_found") from None

    def add(
        self,
        content: str,
        *,
        user_id: str,
        scope: MemoryScope,
        persona_id: Optional[str],
        metadata: Mapping[str, Any],
    ):
        memory_id = f"m{self.next_id}"
        self.next_id += 1
        record = LongTermMemory(
            memory_id,
            content,
            user_id,
            scope,
            persona_id,
            MemoryStatus(metadata.get("status", "active")),
            "2026-09-03T00:00:00+00:00",
            "2026-09-03T00:00:00+00:00",
            dict(metadata),
        )
        self.records[memory_id] = record
        return (record,)

    def update(self, memory_id, *, content=None, metadata=None):
        current = self.get(memory_id)
        merged = dict(current.metadata)
        if metadata is not None:
            merged = dict(metadata)
        updated = replace(
            current,
            content=content if content is not None else current.content,
            status=MemoryStatus(merged.get("status", current.status.value)),
            metadata=merged,
            updated_at="2026-09-04T00:00:00+00:00",
        )
        self.records[memory_id] = updated
        return updated

    def delete(self, memory_id: str) -> None:
        self.get(memory_id)
        del self.records[memory_id]

    def history(self, memory_id: str):
        item = self.get(memory_id)
        return (MemoryHistoryEntry("ADD", item.created_at, None, item.content),)


class FakeExtractor:
    def extract(self, content: str, *, persona_id: str, source_created_at: str = "unknown") -> ExtractedMemories:
        return ExtractedMemories(
            ("User prefers direct answers.",),
            ("May respond well to concrete next steps.",),
        )


def _record(
    memory_id: str,
    content: str,
    *,
    scope: MemoryScope = MemoryScope.SHARED,
    persona_id: Optional[str] = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    user_id: str = "u1",
) -> LongTermMemory:
    return LongTermMemory(
        memory_id,
        content,
        user_id,
        scope,
        persona_id,
        status,
        "2026-09-01T00:00:00+00:00",
        "2026-09-02T00:00:00+00:00",
        {"scope": scope.value, "status": status.value, "source_type": "conversation"},
        0.9,
    )


def _service(tmp_path: Path, backend: FakeBackend):
    operations = MemoryOperationsStore(tmp_path / "operations.sqlite3")
    snapshot = MemorySnapshotWriter(
        backend,
        tmp_path / "memory" / "MEMORY.md",
        user_ids=("u1",),
        persona_names={"coach": "Direct Coach"},
    )
    return MemoryService(backend, operations, snapshot), operations, snapshot


def _settings(tmp_path: Path) -> Settings:
    journal = tmp_path / "journal"
    journal.mkdir(exist_ok=True)
    return Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal),
        RIJI_DATA_DIR=str(tmp_path / "data"),
        DEEPSEEK_API_KEY="test-deepseek-key",
        RIJI_ALLOWED_FEISHU_USER_IDS="u1",
        HERMES_SHARED_SECRET="test-hermes-secret",
        RIJI_MEMORY_PROVIDER="mem0",
        RIJI_MEM0_API_KEY="test-mem0-key-long-enough",
        RIJI_MEMORY_REVIEW_ENABLED=True,
        RIJI_MEMORY_REVIEW_TOKEN="test-review-token-long-enough",
    )


def test_mem0_client_uses_self_hosted_contract_and_scoped_filters() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "m1",
                        "memory": "fact",
                        "user_id": "u1",
                        "agent_id": "coach",
                        "metadata": {"scope": "persona", "status": "active"},
                    }
                ]
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = Mem0Client("http://127.0.0.1:38881", "secret-key", client=http)
    result = client.search(
        "query", user_id="u1", scope=MemoryScope.PERSONA, persona_id="coach"
    )

    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/search"
    assert requests[0].headers["X-API-Key"] == "secret-key"
    assert payload["filters"] == {
        "user_id": "u1",
        "scope": "persona",
        "status": "active",
        "agent_id": "coach",
    }
    assert result[0].persona_id == "coach"


def test_mem0_client_errors_are_sanitized() -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500, text="private"))
    )
    client = Mem0Client("http://127.0.0.1:38881", "secret-key", client=http)

    try:
        client.get("memory-with-private-content")
    except MemoryBackendError as exc:
        assert exc.code == "mem0_http_500"
        assert "secret-key" not in str(exc)
        assert "private" not in str(exc)
    else:
        raise AssertionError("expected sanitized Mem0 error")


def test_mem0_client_reads_redacted_runtime_configuration() -> None:
    expected = {
        "llm": {"provider": "deepseek", "config": {"model": "deepseek-chat"}},
        "embedder": {
            "provider": "fastembed",
            "config": {"model": "BAAI/bge-small-zh-v1.5"},
        },
    }
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=expected))
    )
    client = Mem0Client("http://127.0.0.1:38881", "secret-key", client=http)

    assert client.configuration() == expected


def test_retrieval_is_scoped_bounded_and_fail_open(tmp_path: Path) -> None:
    backend = FakeBackend(
        (
            _record("s1", "shared"),
            _record("p1", "private", scope=MemoryScope.PERSONA, persona_id="coach"),
            _record("p2", "leak", scope=MemoryScope.PERSONA, persona_id="other"),
        )
    )
    service, operations, _snapshot = _service(tmp_path, backend)
    context = service.retrieve("anything", user_id="u1", persona_id="coach")
    assert [item.content for item in context.shared] == ["shared"]
    assert [item.content for item in context.persona] == ["private"]

    backend.available = False
    assert service.retrieve("anything", user_id="u1", persona_id="coach").shared == ()
    operations.close()


def test_capture_processor_writes_scopes_and_clears_plaintext(tmp_path: Path) -> None:
    backend = FakeBackend()
    service, operations, snapshot = _service(tmp_path, backend)
    worker_operations = MemoryOperationsStore(tmp_path / "operations.sqlite3")
    processor = CaptureProcessor(backend, worker_operations, FakeExtractor(), snapshot)
    job_id = service.enqueue_capture(
        source_request_id="request-1",
        user_id="u1",
        persona_id="coach",
        session_id="session-1",
        content="I prefer direct answers.",
    )

    assert job_id is not None
    assert processor.process_next() is True
    records = tuple(backend.records.values())
    assert {item.scope for item in records} == {MemoryScope.SHARED, MemoryScope.PERSONA}
    assert next(item for item in records if item.scope is MemoryScope.SHARED).persona_id is None
    assert next(item for item in records if item.scope is MemoryScope.PERSONA).persona_id == "coach"
    assert all(item.metadata.get("captured_at") for item in records)
    assert operations.get_job(job_id).content is None
    assert operations.get_job(job_id).status.value == "succeeded"
    assert (tmp_path / "memory" / "MEMORY.md").is_file()
    operations.close()
    worker_operations.close()


def test_capture_queue_reclaims_interrupted_processing_job(tmp_path: Path) -> None:
    path = tmp_path / "operations.sqlite3"
    operations = MemoryOperationsStore(path)
    job_id = operations.enqueue(
        source_request_id="interrupted-request",
        user_id="u1",
        persona_id="coach",
        session_id="session-1",
        content="Remember this preference.",
    )

    assert operations.claim_next().id == job_id
    reclaimed = operations.claim_next(stale_after_seconds=0)

    assert reclaimed is not None and reclaimed.id == job_id
    assert reclaimed.attempts == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    operations.close()


def test_snapshot_contains_only_active_memory_and_is_private(tmp_path: Path) -> None:
    backend = FakeBackend(
        (
            _record("s1", "active shared"),
            _record("a1", "archived secret", status=MemoryStatus.ARCHIVED),
            _record("p1", "private observation", scope=MemoryScope.PERSONA, persona_id="coach"),
        )
    )
    writer = MemorySnapshotWriter(
        backend,
        tmp_path / "memory" / "MEMORY.md",
        user_ids=("u1",),
        persona_names={"coach": "Direct Coach"},
    )
    _generated, count = writer.write()
    text = writer.path.read_text(encoding="utf-8")

    assert count == 2
    assert "active shared" in text
    assert "private observation" in text
    assert "archived secret" not in text
    assert "read_only: true" in text
    assert stat.S_IMODE(writer.path.stat().st_mode) == 0o600


def test_snapshot_flattens_multiline_content_without_changing_backend(tmp_path: Path) -> None:
    backend = FakeBackend((_record("s1", "first line\nsecond line"),))
    writer = MemorySnapshotWriter(
        backend,
        tmp_path / "memory" / "MEMORY.md",
        user_ids=("u1",),
        persona_names={},
    )

    writer.write()

    assert "[s1] first line second line" in writer.path.read_text(encoding="utf-8")
    assert backend.get("s1").content == "first line\nsecond line"


def test_snapshot_redacts_credential_like_memory(tmp_path: Path) -> None:
    backend = FakeBackend((_record("s1", "API key: private-value"),))
    writer = MemorySnapshotWriter(
        backend,
        tmp_path / "memory" / "MEMORY.md",
        user_ids=("u1",),
        persona_names={},
    )

    _generated, count = writer.write()
    text = writer.path.read_text(encoding="utf-8")

    assert count == 1
    assert "private-value" not in text
    assert "credential-like content" in text


def test_snapshot_consistency_requires_current_matching_count(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text(
        "source: mem0\nread_only: true\n\n- [m1] one\n- [m2] two\n",
        encoding="utf-8",
    )

    assert _snapshot_consistency_ok(path, {"status": "current", "memory_count": 2})
    assert not _snapshot_consistency_ok(path, {"status": "current", "memory_count": 1})
    assert not _snapshot_consistency_ok(path, {"status": "pending", "memory_count": 2})
    assert not _snapshot_consistency_ok(
        path,
        {
            "status": "current",
            "memory_count": 2,
            "updated_at": "2020-01-01T00:00:00+00:00",
        },
    )


def test_auto_capture_accepts_user_statements_but_excludes_control_requests() -> None:
    assert should_auto_capture("我长期偏好直接的回答") is True
    assert should_auto_capture("帮我记录一下今天发生的事") is True
    assert should_auto_capture("帮我记住我长期偏好直接的回答") is True
    assert should_auto_capture("确认保存 draft-1") is False
    assert should_auto_capture("/memory") is False
    assert should_auto_capture("我的 API key 是 private-value") is False


def test_extractor_rejects_credentials_even_if_model_returns_them() -> None:
    extracted = DeepSeekMemoryExtractor._parse(
        json.dumps(
            {
                "shared": ["用户偏好中文", "用户的 API key 是 private-value"],
                "persona": ["可能需要提醒用户验证码为 123456"],
            }
        )
    )

    assert extracted.shared == ("用户偏好中文",)
    assert extracted.persona == ()


def test_migration_is_idempotent_and_creates_backup(tmp_path: Path) -> None:
    legacy = MemoryStore(tmp_path / "memory.sqlite3")
    candidate = legacy.add_candidate("u1", "coach", "legacy fact")
    legacy.confirm_candidate(candidate)
    legacy.set_preference("u1", "language", "Chinese")
    legacy.set_preference("u1", "current_persona", "coach")
    backend = FakeBackend()
    service, operations, _snapshot = _service(tmp_path, backend)
    migrator = MemoryMigrator(legacy, backend, operations, service)

    dry_run = migrator.run(apply=False)
    first = migrator.run(apply=True)
    second = migrator.run(apply=True)

    assert dry_run.discovered == 2
    assert first.migrated == 2
    assert first.backup_path is not None and first.backup_path.is_file()
    assert second.migrated == 0
    assert len(backend.records) == 2
    assert all("current_persona" not in item.content for item in backend.records.values())
    with MemoryStore(first.backup_path) as restored:
        assert [item.content for item in restored.list_confirmed_memories("u1")] == [
            "legacy fact"
        ]
    legacy.close()
    operations.close()


def test_review_requires_login_and_csrf_and_can_archive(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    backend = FakeBackend((_record("m1", "review me"),))
    service, operations, _snapshot = _service(tmp_path, backend)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)

    assert client.get("/admin/memory", follow_redirects=False).status_code == 303
    assert client.post("/admin/memory/login", json={"token": "wrong"}).status_code == 401
    assert client.post(
        "/admin/memory/login", json={"token": "test-review-token-long-enough"}
    ).status_code == 200
    page = client.get("/admin/memory")
    assert page.status_code == 200
    assert "review me" in page.text
    assert page.headers["Cache-Control"] == "no-store"

    endpoint = "/admin/memory/api/memories/m1/archive"
    assert client.post(endpoint, json={"user_id": "u1"}).status_code == 403
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match is not None
    response = client.post(
        endpoint,
        json={"user_id": "u1"},
        headers={"X-CSRF-Token": match.group(1)},
    )
    assert response.status_code == 200
    assert backend.get("m1").status is MemoryStatus.ARCHIVED
    operations.close()


def test_review_session_cookie_is_derived_not_raw_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    backend = FakeBackend()
    service, operations, _snapshot = _service(tmp_path, backend)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    response = client.post(
        "/admin/memory/login", json={"token": "test-review-token-long-enough"}
    )
    expected = hmac.new(
        b"test-review-token-long-enough", b"session", hashlib.sha256
    ).hexdigest()
    assert client.cookies.get("riji_memory_admin") == expected
    assert "test-review-token-long-enough" not in response.headers.get("set-cookie", "")
    operations.close()


def _authenticated_review(tmp_path: Path):
    settings = _settings(tmp_path)
    backend = FakeBackend((_record("m1", "review me"),))
    service, operations, _snapshot = _service(tmp_path, backend)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    client.post("/admin/memory/login", json={"token": "test-review-token-long-enough"})
    page = client.get("/admin/memory?selected=m1")
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match is not None and "选中记忆历史" in page.text
    return backend, service, operations, client, {"X-CSRF-Token": match.group(1)}


def test_review_snapshot_history_and_dead_letter_retry(tmp_path: Path) -> None:
    _backend, _service, operations, client, headers = _authenticated_review(tmp_path)
    job_id = operations.enqueue(
        source_request_id="dead-letter-request",
        user_id="u1",
        persona_id="coach",
        session_id="session-1",
        content="remember me",
    )
    operations.claim_next()
    operations.mark_failed(job_id, "mem0_unavailable", max_attempts=1)

    assert client.post(
        "/admin/memory/api/jobs/{}/retry".format(job_id), headers=headers
    ).status_code == 200
    assert operations.get_job(job_id).status.value == "retry"
    assert client.post("/admin/memory/api/snapshot", headers=headers).status_code == 200
    operations.close()


def test_review_update_archive_restore_and_delete(tmp_path: Path) -> None:
    backend, _service, operations, client, headers = _authenticated_review(tmp_path)
    endpoint = "/admin/memory/api/memories/m1/{}"
    assert client.post(
        endpoint.format("update"),
        json={"user_id": "u1", "content": "corrected"},
        headers=headers,
    ).status_code == 200
    assert client.post(
        endpoint.format("archive"), json={"user_id": "u1"}, headers=headers
    ).status_code == 200
    assert client.post(
        endpoint.format("restore"), json={"user_id": "u1"}, headers=headers
    ).status_code == 200
    assert client.post(
        endpoint.format("delete"), json={"user_id": "u1"}, headers=headers
    ).status_code == 400
    assert client.post(
        endpoint.format("delete"),
        json={"user_id": "u1", "confirmation": "DELETE"},
        headers=headers,
    ).status_code == 200
    with pytest.raises(MemoryBackendError):
        backend.get("m1")
    assert {item.action for item in operations.list_changes(user_id="u1")} >= {
        "UPDATE", "ARCHIVE", "RESTORE", "DELETE"
    }
    operations.close()
