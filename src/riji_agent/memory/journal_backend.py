"""Evidence-aware access to the existing authoritative memory backend."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional, Sequence

from riji_agent.memory.backend import LongTermMemoryBackend, MemoryBackendError
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_types import content_key
from riji_agent.memory.models import CaptureJob, LongTermMemory, MemoryHistoryEntry, MemoryScope, MemoryStatus


class JournalEvidenceBackend:
    def __init__(self, backend: LongTermMemoryBackend, engine: JournalMemoryEngine) -> None:
        self.raw, self.engine = backend, engine

    def health(self) -> bool:
        return self.raw.health()

    def configuration(self) -> Mapping[str, Any]:
        return self.raw.configuration()  # type: ignore[attr-defined]

    def is_suppressed(self, content: str, *, user_id: str) -> bool:
        return user_id == self.engine.policy.user_id and self.engine.store.suppressed(content_hash=content_key(content))

    def search(self, query: str, *, user_id: str, scope: MemoryScope,
               persona_id: Optional[str] = None, limit: int = 8) -> Sequence[LongTermMemory]:
        records = self.raw.search(query, user_id=user_id, scope=scope, persona_id=persona_id, limit=min(limit * 3, 100))
        candidates = {item.id: item for item in records if self._matches(item, user_id, scope, persona_id)}
        if user_id == self.engine.policy.user_id and scope is MemoryScope.SHARED:
            self._add_related(candidates)
        result = [self._decorate(item) for item in candidates.values() if self.engine.is_valid(item)
                  and item.status is MemoryStatus.ACTIVE]
        historical = any(word in query for word in ("当时", "过去", "之前", "历史"))
        ranks = {"current": 0, "conflict": 1, "historical": 0 if historical else 2}
        return tuple(sorted(result, key=lambda item: ranks.get(item.metadata.get("journal_state"), 0))[:limit])

    def _add_related(self, candidates: dict[str, LongTermMemory]) -> None:
        linked: set[str] = set()
        for item in tuple(candidates.values()):
            row = self.engine.store.record(item.id)
            if row:
                linked.update(r["id"] for r in self.engine.store.rows("SELECT id FROM records WHERE series=?", (row["series"],)))
            for rel in self.engine.store.rows("SELECT * FROM relations WHERE source_id=? OR target_id=?", (item.id, item.id)):
                linked.update((rel["source_id"], rel["target_id"]))
        for memory_id in sorted(linked)[:40]:
            if memory_id in candidates:
                continue
            try:
                item = self.raw.get(memory_id)
            except MemoryBackendError:
                continue
            if self._matches(item, self.engine.policy.user_id, MemoryScope.SHARED, None):
                candidates[item.id] = item

    @staticmethod
    def _matches(item: LongTermMemory, user_id: str, scope: MemoryScope,
                 persona_id: Optional[str]) -> bool:
        return item.user_id == user_id and item.scope is scope and (
            scope is MemoryScope.SHARED or item.persona_id == persona_id)

    def _decorate(self, item: LongTermMemory) -> LongTermMemory:
        row = self.engine.store.record(item.id)
        if row is None:
            return replace(item, metadata=dict(item.metadata, journal_state="pending")) if item.metadata.get("journal_managed") else item
        state = row["state"]
        valid = self.engine.is_valid(item)
        if not valid:
            state = "source_invalid"
        elif state == "current" and row["valid_from"]:
            later = self.engine.store.rows("SELECT id FROM records WHERE series=? AND valid_from>? AND state='current'",
                                           (row["series"], row["valid_from"]))
            for candidate in later:
                try:
                    current = self.raw.get(candidate["id"])
                    if current.status is MemoryStatus.ACTIVE and self.engine.is_valid(current):
                        state = "historical"
                        break
                except MemoryBackendError:
                    continue
        metadata = dict(item.metadata, journal_state=state, valid_from=row["valid_from"],
                        journal_series=row["series"], manually_corrected=bool(row["protected"]))
        metadata.update(self._current_provenance(item, row))
        return replace(item, metadata=metadata)

    def _current_provenance(self, item: LongTermMemory, record: dict[str, Any]) -> dict[str, Any]:
        if record["protected"]:
            return {"source_id": "manual/" + item.id, "source_type": "user-correction",
                    "source_created_at": item.metadata.get("reviewed_at")}
        sources = []
        for row in self.engine.store.rows("SELECT evidence_id FROM supports WHERE memory_id=?", (item.id,)):
            try:
                evidence = self.engine.current_evidence(row["evidence_id"])
                sources.append({"source_id": "riji/" + evidence.path[:-3], "line": evidence.line,
                                "section": evidence.section, "version": evidence.version,
                                "observed_at": evidence.observed_at, "kind": evidence.kind})
            except Exception:
                continue
        native = self.engine.store.rows("SELECT * FROM native_supports WHERE memory_id=? AND user_id=?", (item.id, item.user_id))
        metadata: dict[str, Any] = {"journal_evidence": sources, "native_evidence": native}
        if sources:
            metadata.update(source_id=sources[0]["source_id"], source_created_at=sources[0]["observed_at"],
                            source_type="journal" if sources[0]["kind"] == "daily" else "journal-summary")
        elif native:
            metadata.update(source_id=native[0]["source_id"], source_created_at=native[0]["observed_at"], source_type="conversation")
        return metadata

    def attach_native_support(self, memory_id: str, job: CaptureJob) -> None:
        item = self.get(memory_id)
        if item.user_id != job.user_id or item.scope is not MemoryScope.SHARED or not item.metadata.get("journal_managed"):
            return
        source_id = f"conversation/{job.source_message_id}" if job.source_message_id is not None else f"conversation/request/{job.source_request_id}"
        self.engine.store.execute("INSERT OR IGNORE INTO native_supports VALUES (?,?,?,?,?)",
                                  (memory_id, job.user_id, job.source_request_id, source_id, job.source_created_at))
        self.engine.store.bump_epoch()

    def list_memories(self, *, user_id: str, persona_id: Optional[str] = None,
                      include_archived: bool = False, limit: int = 1000) -> Sequence[LongTermMemory]:
        exporter = getattr(self.raw, "export_memories", None)
        records = exporter(user_id=user_id) if exporter else self.raw.list_memories(
            user_id=user_id, persona_id=persona_id, include_archived=include_archived, limit=limit)
        if not exporter and len(records) >= limit:
            raise MemoryBackendError("incomplete_memory_listing")
        result = []
        for item in records:
            if item.user_id != user_id or (persona_id and item.persona_id != persona_id):
                continue
            if user_id == self.engine.policy.user_id and self.engine.store.suppressed(memory_id=item.id):
                continue
            if not include_archived and item.status is not MemoryStatus.ACTIVE:
                continue
            if include_archived or self.engine.is_valid(item):
                result.append(self._decorate(item))
        if len(result) > limit:
            raise MemoryBackendError("memory_listing_limit_exceeded")
        return tuple(result)

    def export_memories(self, *, user_id: str) -> Sequence[LongTermMemory]:
        exporter = getattr(self.raw, "export_memories", None)
        records = exporter(user_id=user_id) if exporter else self.raw.list_memories(
            user_id=user_id, include_archived=True, limit=1000)
        if not exporter and len(records) >= 1000:
            raise MemoryBackendError("incomplete_memory_export")
        return tuple(self._decorate(item) for item in records if item.user_id == user_id
                     and not self.engine.store.suppressed(memory_id=item.id)
                     and (not item.metadata.get("journal_managed") or self.engine.store.record(item.id)))

    def get(self, memory_id: str) -> LongTermMemory:
        item = self.raw.get(memory_id)
        if item.user_id == self.engine.policy.user_id and self.engine.store.suppressed(memory_id=memory_id):
            raise MemoryBackendError("memory_not_found")
        return self._decorate(item)

    def add(self, content: str, *, user_id: str, scope: MemoryScope,
            persona_id: Optional[str], metadata: Mapping[str, Any]) -> Sequence[LongTermMemory]:
        if self.engine.store.get_control("restore_in_progress"):
            raise MemoryBackendError("memory_restore_in_progress")
        if self.is_suppressed(content, user_id=user_id):
            return ()
        result = self.raw.add(content, user_id=user_id, scope=scope, persona_id=persona_id, metadata=metadata)
        self.engine.store.bump_epoch()
        return result

    def update(self, memory_id: str, *, content: Optional[str] = None,
               metadata: Optional[Mapping[str, Any]] = None) -> LongTermMemory:
        before = self.get(memory_id)
        if self.engine.store.get_control("restore_in_progress"):
            raise MemoryBackendError("memory_restore_in_progress")
        updated = self.raw.update(memory_id, content=content, metadata=metadata)
        self.engine.store.bump_epoch()
        if content is not None and before.user_id == self.engine.policy.user_id:
            self.engine._register_target(before)
            self.engine.store.execute("INSERT OR IGNORE INTO content_aliases VALUES (?,?)", (memory_id, content_key(content)))
            self.engine.store.execute("UPDATE records SET protected=1,content_hash=?,state='current' WHERE id=?",
                                      (content_key(content), memory_id))
        return self._decorate(updated)

    def delete(self, memory_id: str) -> None:
        item = self.get(memory_id)
        self.engine.store.bump_epoch()
        if item.user_id == self.engine.policy.user_id:
            self.engine._register_target(item)
            self.engine.store.suppress(memory_id, content_key(item.content))
        self.engine.erase_backend_memory(memory_id)
        if item.user_id == self.engine.policy.user_id:
            self.engine.store.execute("UPDATE cleanup SET status='done' WHERE memory_id=?", (memory_id,))

    def history(self, memory_id: str) -> Sequence[MemoryHistoryEntry]:
        self.get(memory_id)
        return self.raw.history(memory_id)
