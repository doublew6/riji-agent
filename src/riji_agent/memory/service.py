"""Application service for scoped retrieval, capture and reviewed mutations."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from riji_agent.memory.backend import LongTermMemoryBackend, MemoryBackendError
from riji_agent.memory.capture import DeepSeekMemoryExtractor, ExtractedMemories, should_auto_capture
from riji_agent.memory.models import (
    LongTermMemory,
    CaptureJob,
    MemoryScope,
    MemoryStatus,
    NewMemoryChange,
)
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.model_call import deferred_model_error
from riji_agent.memory.organization import overdue_ids
from riji_agent.memory.snapshot import MemorySnapshotWriter
from riji_agent.memory.render import render_memory_fact
from riji_agent.models.types import LLMError

_LOG = logging.getLogger("riji_agent.memory")

if TYPE_CHECKING:
    from riji_agent.memory.journal_engine import JournalMemoryEngine


@dataclass(frozen=True)
class RetrievedMemoryContext:
    shared: Sequence[LongTermMemory]
    persona: Sequence[LongTermMemory]
    notice: str = ""


class MemoryService:
    def __init__(
        self,
        backend: LongTermMemoryBackend,
        operations: MemoryOperationsStore,
        snapshot: Optional[MemorySnapshotWriter],
        *,
        context_max_chars: int = 2000,
        auto_capture: bool = True,
    ) -> None:
        self.backend = backend
        self.operations = operations
        self.snapshot = snapshot
        self._context_max_chars = context_max_chars
        self._auto_capture = auto_capture
        self.journal: Optional[JournalMemoryEngine] = None

    def retrieve(
        self, query: str, *, user_id: str, persona_id: str
    ) -> RetrievedMemoryContext:
        try:
            shared = self.backend.search(
                query, user_id=user_id, scope=MemoryScope.SHARED, limit=12
            )
            private = self.backend.search(
                query,
                user_id=user_id,
                scope=MemoryScope.PERSONA,
                persona_id=persona_id,
                limit=12,
            )
        except MemoryBackendError:
            _LOG.warning("long-term memory retrieval unavailable")
            return RetrievedMemoryContext((), (), "长期记忆后端暂不可用，不能把检索失败解释成用户从未记录。")
        if self.journal is not None:
            mentors = self.journal.policy.mentors
            def allowed(item: LongTermMemory) -> bool:
                return self.journal.can_send(item) and (
                    not item.metadata.get("journal_managed") or not mentors or persona_id in mentors)
            shared = tuple(item for item in shared if allowed(item))
            private = tuple(item for item in private if allowed(item))
        return self._enrich_context(shared, private, user_id)

    def _enrich_context(self, shared: Sequence[LongTermMemory], private: Sequence[LongTermMemory],
                        user_id: str) -> RetrievedMemoryContext:
        try:
            return self._organized_context(shared, private, user_id)
        except Exception:
            _LOG.warning("memory organization context unavailable")
            result = self._bound_context(shared, private)
            return RetrievedMemoryContext(result.shared, result.persona,
                                          "记忆整理或覆盖状态暂不可用，仅使用当前有效事实，不能据此声称完整回顾了历史。")

    def _organized_context(self, shared: Sequence[LongTermMemory], private: Sequence[LongTermMemory],
                           user_id: str) -> RetrievedMemoryContext:
        latest = self.operations.organization.latest(user_id, ready_only=True)
        due = overdue_ids(latest["report"] if latest else None, (*shared, *private))
        from riji_agent.memory.journal_organization import recall_observations
        shared_observations = recall_observations(self, shared, user_id)
        private_observations = recall_observations(self, private, user_id)
        result = self._bound_context(
            (*shared_observations, *sorted(shared, key=lambda item: item.id in due)),
            (*private_observations, *sorted(private, key=lambda item: item.id in due)),
        )
        notice = self.journal.coverage_notice() if self.journal is not None else ""
        return RetrievedMemoryContext(result.shared, result.persona, notice)

    def enqueue_capture(
        self,
        *,
        source_request_id: str,
        user_id: str,
        persona_id: str,
        session_id: str,
        content: str,
        source_message_id: Optional[int] = None,
        source_created_at: Optional[str] = None,
    ) -> Optional[int]:
        if not self._auto_capture or not should_auto_capture(content):
            return None
        return self.operations.enqueue(
            source_request_id=source_request_id,
            user_id=user_id,
            persona_id=persona_id,
            session_id=session_id,
            content=content,
            source_message_id=source_message_id,
            source_created_at=source_created_at,
        )

    def list_memories(
        self, *, user_id: str, include_archived: bool = True
    ) -> Sequence[LongTermMemory]:
        return self.backend.list_memories(
            user_id=user_id, include_archived=include_archived, limit=100000 if self.journal else 1000
        )

    def update_memory(self, memory_id: str, *, user_id: str, content: str) -> None:
        before = self._owned(memory_id, user_id)
        metadata = dict(before.metadata, reviewed_at=datetime.now(timezone.utc).isoformat())
        updated = self.backend.update(memory_id, content=content, metadata=metadata)
        self._record("UPDATE", before, after=updated.content)
        self.operations.organization.request(user_id)
        self.request_snapshot()

    def reconfirm_memory(self, memory_id: str, *, user_id: str) -> None:
        before = self._owned(memory_id, user_id)
        metadata = dict(before.metadata, reviewed_at=datetime.now(timezone.utc).isoformat())
        self.backend.update(memory_id, metadata=metadata)
        self._record("RECONFIRM", before, after=before.content)
        self.operations.organization.request(user_id)
        self.request_snapshot()

    def archive_memory(self, memory_id: str, *, user_id: str) -> None:
        self._set_status(memory_id, user_id=user_id, status=MemoryStatus.ARCHIVED)

    def restore_memory(self, memory_id: str, *, user_id: str) -> None:
        self._set_status(memory_id, user_id=user_id, status=MemoryStatus.ACTIVE)

    def delete_memory(self, memory_id: str, *, user_id: str) -> None:
        before = self._owned(memory_id, user_id)
        failure = None
        try:
            self.backend.delete(memory_id)
        except MemoryBackendError as exc:
            if self.journal is None or not self.journal.store.suppressed(memory_id=memory_id):
                raise
            failure = exc
        self._record("DELETE", before, after=None)
        self.operations.erase_memory_text(memory_id, user_id)
        if self.snapshot:
            self.snapshot.invalidate()
        self.operations.organization.request(user_id)
        self.request_snapshot()
        if failure:
            raise MemoryBackendError("memory_cleanup_pending") from None

    def request_snapshot(self) -> None:
        if self.snapshot is None:
            return
        self.operations.mark_snapshot_pending()
        try:
            if self.journal is not None:
                self.snapshot.invalidate()
            self.refresh_snapshot()
        except Exception:
            self.operations.mark_snapshot_failed("snapshot_refresh_failed")
            _LOG.warning("MEMORY.md refresh failed")

    def refresh_snapshot(self) -> tuple[str, int]:
        if self.snapshot is None:
            raise MemoryBackendError("snapshot_disabled")
        generated_at, count = self.snapshot.write()
        self.operations.mark_snapshot_succeeded(
            generated_at=generated_at, memory_count=count
        )
        return generated_at, count

    def _set_status(
        self, memory_id: str, *, user_id: str, status: MemoryStatus
    ) -> None:
        before = self._owned(memory_id, user_id)
        metadata = dict(before.metadata)
        metadata["status"] = status.value
        updated = self.backend.update(memory_id, metadata=metadata)
        action = "ARCHIVE" if status is MemoryStatus.ARCHIVED else "RESTORE"
        self._record(action, before, after=updated.content)
        self.operations.organization.request(user_id)
        self.request_snapshot()

    def _owned(self, memory_id: str, user_id: str) -> LongTermMemory:
        memory = self.backend.get(memory_id)
        if memory.user_id != user_id:
            raise MemoryBackendError("memory_not_found")
        return memory

    def _record(
        self, action: str, before: LongTermMemory, *, after: Optional[str]
    ) -> None:
        self.operations.record_change(
            NewMemoryChange(
                memory_id=before.id,
                user_id=before.user_id,
                persona_id=before.persona_id,
                scope=before.scope,
                action=action,
                before=None if action == "DELETE" else before.content,
                after=after,
            )
        )

    def _bound_context(
        self,
        shared: Sequence[LongTermMemory],
        persona: Sequence[LongTermMemory],
    ) -> RetrievedMemoryContext:
        seen: set[str] = set()
        kept_shared: list[LongTermMemory] = []
        kept_persona: list[LongTermMemory] = []
        remaining = self._context_max_chars
        for target, records in ((kept_shared, shared), (kept_persona, persona)):
            for item in records:
                key = " ".join(item.content.lower().split())
                cost = len(render_memory_fact(item))
                if not key or key in seen or cost > remaining:
                    continue
                seen.add(key)
                target.append(item)
                remaining -= cost
        return RetrievedMemoryContext(tuple(kept_shared), tuple(kept_persona))


class CaptureProcessor:
    def __init__(
        self,
        backend: LongTermMemoryBackend,
        operations: MemoryOperationsStore,
        extractor: DeepSeekMemoryExtractor,
        snapshot: Optional[MemorySnapshotWriter],
    ) -> None:
        self._backend = backend
        self._operations = operations
        self._extractor = extractor
        self._snapshot = snapshot

    def process_next(self) -> bool:
        job = self._operations.claim_next()
        if job is None:
            try:
                self._refresh_pending_snapshot()
            except Exception:
                _LOG.warning("background MEMORY.md refresh failed")
            return False
        try:
            if not job.content:
                raise MemoryBackendError("capture_payload_missing")
            extracted = self._extract_job(job)
            self._store_items(job, extracted.shared, MemoryScope.SHARED)
            self._store_items(job, extracted.persona, MemoryScope.PERSONA)
            if self._snapshot is not None:
                self._operations.mark_snapshot_pending()
                self._refresh_pending_snapshot()
            self._operations.mark_succeeded(job.id)
            if extracted.shared or extracted.persona:
                self._operations.organization.request(job.user_id)
        except (MemoryBackendError, LLMError, OSError) as exc:
            deferred = deferred_model_error(exc)
            if deferred is not None:
                self._operations.defer_model(job.id, *deferred)
                _LOG.warning("long-term memory capture deferred code=%s", deferred[0])
                return True
            code = getattr(exc, "code", "capture_processing_failed")
            self._operations.mark_failed(job.id, code)
            _LOG.warning("long-term memory capture failed code=%s", code)
        except Exception:
            self._operations.mark_failed(job.id, "capture_processing_failed")
            _LOG.warning("long-term memory capture failed code=capture_processing_failed")
        return True

    def _extract_job(self, job: CaptureJob) -> ExtractedMemories:
        if job.extracted_json is not None:
            return DeepSeekMemoryExtractor._parse(job.extracted_json)
        extracted = self._extractor.extract(
            job.content, persona_id=job.persona_id,
            source_created_at=job.source_created_at or job.created_at,
        )
        self._operations.save_extraction(
            job.id, json.dumps({"shared": list(extracted.shared), "persona": list(extracted.persona)})
        )
        if not extracted.shared and not extracted.persona:
            self._operations.record_change(NewMemoryChange(
                memory_id="", user_id=job.user_id, persona_id=job.persona_id,
                scope=MemoryScope.PERSONA, action="NO_DURABLE_FACT",
                before=None, after=None, source_request_id=job.source_request_id,
            ))
        return extracted

    def _store_items(self, job: CaptureJob, items: Iterable[str], scope: MemoryScope) -> None:
        existing = self._backend.list_memories(
            user_id=job.user_id,
            persona_id=job.persona_id if scope is MemoryScope.PERSONA else None,
            include_archived=True,
            limit=1000,
        )
        existing_ids = {
            item.metadata.get("source_item_id")
            for item in existing
            if item.scope is scope
        }
        existing_text = {
            " ".join(item.content.casefold().split()): item
            for item in existing if item.scope is scope
        }
        for index, content in enumerate(items):
            suppressed = getattr(self._backend, "is_suppressed", None)
            if suppressed is not None and suppressed(content, user_id=job.user_id):
                continue
            item_id = _source_item_id(job.source_request_id, scope, index, content)
            normalized = " ".join(content.casefold().split())
            if item_id in existing_ids:
                continue
            if normalized in existing_text:
                matched = existing_text[normalized]
                attach = getattr(self._backend, "attach_native_support", None)
                if attach is not None:
                    attach(matched.id, job)
                self._operations.record_change(NewMemoryChange(
                    memory_id=matched.id, user_id=job.user_id, persona_id=matched.persona_id,
                    scope=scope, action="DEDUP_SKIP", before=matched.content,
                    after=content, source_request_id=job.source_request_id,
                ))
                continue
            added = self._add_item(job, content, scope, item_id)
            existing_text[normalized] = added
            existing_ids.add(item_id)

    def _add_item(self, job, content: str, scope: MemoryScope, item_id: str) -> LongTermMemory:
        metadata = {
            "source_request_id": job.source_request_id,
            "source_item_id": item_id,
            "source_type": "conversation" if scope is MemoryScope.SHARED else "mentor-observation",
            "scope": scope.value,
            "persona_id": job.persona_id if scope is MemoryScope.PERSONA else None,
            "session_id": job.session_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "status": MemoryStatus.ACTIVE.value,
        }
        if job.source_message_id is not None:
            metadata["source_message_id"] = job.source_message_id
            metadata["source_id"] = f"conversation/{job.source_message_id}"
        if job.source_created_at is not None:
            metadata["source_created_at"] = job.source_created_at
        added = self._backend.add(
            content,
            user_id=job.user_id,
            scope=scope,
            persona_id=job.persona_id if scope is MemoryScope.PERSONA else None,
            metadata=metadata,
        )
        if not added:
            raise MemoryBackendError("mem0_add_empty")
        for item in added:
            self._operations.record_change(
                NewMemoryChange(
                    memory_id=item.id,
                    user_id=job.user_id,
                    persona_id=(
                        job.persona_id if scope is MemoryScope.PERSONA else None
                    ),
                    scope=scope,
                    action="ADD",
                    before=None,
                    after=content,
                    source_request_id=job.source_request_id,
                )
            )
        return added[0]

    def _refresh_pending_snapshot(self) -> None:
        if self._snapshot is None:
            return
        state = self._operations.snapshot_state()
        if state.get("status") not in {"pending", "failed"}:
            return
        try:
            generated_at, count = self._snapshot.write()
            self._operations.mark_snapshot_succeeded(
                generated_at=generated_at, memory_count=count
            )
        except (MemoryBackendError, OSError):
            self._operations.mark_snapshot_failed("snapshot_refresh_failed")
            raise


def _source_item_id(
    request_id: str, scope: MemoryScope, index: int, content: str
) -> str:
    raw = f"{request_id}:{scope.value}:{index}:{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
