"""Historical and incremental journal processing with explicit memory writes."""

from __future__ import annotations

import json
import fcntl
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional, Sequence

from riji_agent.memory.backend import LongTermMemoryBackend, MemoryBackendError
from riji_agent.memory.journal_extract import JournalMemoryExtractor, parse_candidate, parse_decisions
from riji_agent.memory.journal_sources import discover_sources, read_source
from riji_agent.memory.journal_store import JournalMemoryStore
from riji_agent.memory.journal_types import (
    JournalCandidate, JournalEvidence, JournalMemoryError, JournalMemoryPolicy, content_key, fingerprint, utc_now,
)
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.memory.model_call import deferred_model_error
from riji_agent.memory.organization import memory_version
from riji_agent.models.types import LLMProvider


@dataclass(frozen=True)
class _Write:
    operation_id: str
    candidate: JournalCandidate
    evidence: JournalEvidence
    decision: dict[str, Any]
    target: Optional[LongTermMemory]


class JournalMemoryEngine:
    def __init__(self, policy: JournalMemoryPolicy, store: JournalMemoryStore,
                 backend: LongTermMemoryBackend, provider: LLMProvider) -> None:
        self.policy, self.store, self.backend, self.provider = policy, store, backend, provider
        self.store.configure(policy)
        self._last_scan = 0.0
        self.on_change: Callable[[], None] = lambda: None
        from riji_agent.memory.journal_privacy import JournalPrivacy
        self.privacy = JournalPrivacy(self)

    def scan(self) -> dict[str, Any]:
        if not self.policy.enabled:
            return self.store.progress()
        with self.store.path.with_suffix(".scan.lock").open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.store.progress()
            try:
                return self._scan_sources()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _scan_sources(self) -> dict[str, Any]:
        generation = uuid.uuid4().hex
        before = self._source_manifest()
        try:
            for source in discover_sources(self.policy):
                self.store.record_source(source, generation)
            self.store.finish_scan(generation)
        except JournalMemoryError as exc:
            self.store.set_control("scan_error", exc.code)
        self._last_scan = time.monotonic()
        self.store.set_control("scan_requested", "0")
        self.advance_initialization()
        if before != self._source_manifest():
            self.notify_change()
        return self.store.progress()

    def notify_change(self) -> None:
        self.store.bump_epoch()
        self.on_change()

    def initialization_status(self) -> dict[str, Any]:
        return self.store.initialization_status(self.policy)

    def advance_initialization(self) -> None:
        """Maintain the fixed batch without reading new sources or calling models."""
        if not self.privacy.allows("history"):
            return
        self.store.start_initialization(self.policy)
        if not self.store.initialization_active(self.policy):
            self.store.refresh_initialization(self.policy, organization_allowed=self.privacy.allows("organization"))
            return
        organization = self.privacy.allows("organization")
        if organization and not self._track_initialization_memories():
            return
        self.store.refresh_initialization(self.policy, organization_allowed=organization)

    def _track_initialization_memories(self) -> bool:
        rows = self.store.rows("SELECT DISTINCT o.memory_id FROM operations o "
            "JOIN initialization_evidence i ON i.id=o.evidence_id "
            "JOIN evidence e ON e.id=i.id AND e.version=i.version "
            "WHERE o.status='applied' AND o.memory_id<>'' AND e.active=1 "
            "AND i.state IN ('pending','succeeded') AND NOT EXISTS "
            "(SELECT 1 FROM initialization_seeds s WHERE s.id=o.memory_id)")
        for row in rows:
            try:
                item = self.backend.get(row["memory_id"])
                if self.can_send(item, "organization"):
                    self.store.track_initialization_seed(self.policy, item.id, memory_version(item))
            except MemoryBackendError as exc:
                if exc.code != "memory_not_found":
                    return False
        return True

    def _source_manifest(self) -> list[dict[str, Any]]:
        return self.store.rows("SELECT id,version,status,reason FROM sources ORDER BY id")

    def coverage_notice(self) -> str:
        if not self.policy.enabled:
            return "日记记忆提取已停用；不可据此声称已完整检查历史日记。"
        if (self.store.get_control("scan_error") or not self.store.get_control("initial_scan_complete")
                or self.store.rows("SELECT id FROM evidence WHERE active=1 AND status NOT IN ('succeeded','suppressed') LIMIT 1")
                or self.store.rows("SELECT id FROM sources WHERE status='failed' LIMIT 1")):
            return "历史日记记忆仍有未处理或暂不可读的范围。依据不足时说明覆盖尚不完整，不猜测未记录的事实。"
        return "仅使用已授权区块形成的记忆；检索未命中不能证明用户从未记录。"

    def process_next(self) -> bool:
        if self.process_cleanup():
            return True
        if not self.policy.enabled:
            return False
        if self.store.get_control("restore_in_progress"):
            return False
        if (time.monotonic() - self._last_scan >= self.policy.scan_seconds
                or self.store.get_control("scan_requested") == "1"):
            self.scan()
        self.advance_initialization()
        job = self.privacy.claim()
        if job is None:
            return False
        try:
            self._process(job)
            if self.store.initialization_active(self.policy):
                self._track_initialization_memories()
            self.store.succeed(job)
            self.notify_change()
        except Exception as exc:
            deferred = deferred_model_error(exc)
            if deferred is not None:
                self.store.defer_model(job, *deferred)
                return True
            code = exc.code if isinstance(exc, (JournalMemoryError, MemoryBackendError)) else "journal_processing_failed"
            self.store.fail(job, code, self.policy.max_attempts)
            if code == "journal_source_changed":
                self._last_scan = 0
        self.advance_initialization()
        return True

    def process_cleanup(self) -> bool:
        rows = self.store.rows("SELECT * FROM cleanup WHERE status='pending' AND available_at<=? LIMIT 1", (utc_now(),))
        if not rows:
            return False
        row = rows[0]
        try:
            self.erase_backend_memory(row["memory_id"])
            self.store.execute("UPDATE cleanup SET status='done',error=NULL WHERE memory_id=?", (row["memory_id"],))
        except MemoryBackendError as exc:
            if exc.code == "memory_not_found":
                self.store.execute("UPDATE cleanup SET status='done' WHERE memory_id=?", (row["memory_id"],))
            else:
                after = (datetime.now(timezone.utc) + timedelta(seconds=min(3600, 10 * 2 ** min(8, row["attempts"])))).isoformat()
                self.store.execute("UPDATE cleanup SET attempts=attempts+1,available_at=?,error=? WHERE memory_id=?",
                                   (after, "memory_cleanup_pending", row["memory_id"]))
        return True

    def erase_backend_memory(self, memory_id: str) -> None:
        links = self.store.rows("SELECT source_id FROM relations WHERE target_id=?", (memory_id,))
        for link in links:
            try:
                related = self.backend.get(link["source_id"])
            except MemoryBackendError as exc:
                if exc.code == "memory_not_found":
                    continue
                raise
            if related.metadata.get("relation_target") == memory_id:
                self.backend.update(related.id, metadata=dict(
                    related.metadata, relation_target=None, relation_reason="Related memory was forgotten."))
        purge = getattr(self.backend, "purge", self.backend.delete)
        purge(memory_id)

    def _process(self, job: dict[str, Any]) -> None:
        self.privacy.check("history", job)
        evidence = self.current_evidence(job["id"], expected_version=job["version"])
        if self.store.suppressed(evidence_id=evidence.id):
            return
        extractor = JournalMemoryExtractor(self.provider, charge=lambda size: self._charge(job, evidence, size))
        candidates = self._extract(job, evidence, extractor)
        if not candidates:
            return
        records, decisions = self._decisions(job, candidates, extractor)
        for candidate, decision in zip(candidates, decisions):
            self.privacy.check("history", job)
            self.store.check_job(job)
            self.current_evidence(evidence.id, expected_version=evidence.version)
            target = records.get(decision["target_id"])
            key = fingerprint(evidence.id + json.dumps(candidate.to_dict(), sort_keys=True, ensure_ascii=False))
            self._apply(_Write(key, candidate, evidence, decision, target), lambda: self.store.check_job(job))

    def _charge(self, job: dict[str, Any], evidence: JournalEvidence, size: int) -> None:
        self.privacy.check("history", job)
        self.store.check_job(job)
        self.current_evidence(evidence.id, expected_version=evidence.version)
        self.store.reserve_budget(self.policy, evidence, size)
        phase = "relation" if job["extracted"] is not None else "extraction"
        self.store.log_call(phase, [evidence.id + ":" + evidence.version], size)

    def _extract(self, job: dict, evidence: JournalEvidence,
                 extractor: JournalMemoryExtractor) -> tuple[JournalCandidate, ...]:
        if job["extracted"] is not None:
            return tuple(parse_candidate(item, evidence) for item in json.loads(job["extracted"]))
        candidates = extractor.extract(evidence)
        self.store.cache(job, "extracted", [item.to_dict() for item in candidates])
        job["extracted"] = json.dumps([item.to_dict() for item in candidates], ensure_ascii=False)
        return candidates

    def _decisions(self, job: dict, candidates: Sequence[JournalCandidate],
                   extractor: JournalMemoryExtractor) -> tuple[dict[str, LongTermMemory], list[dict]]:
        if job["decisions"] is not None:
            cached = json.loads(job["decisions"])
            records = self._load_targets(cached["versions"])
            if set(records) != set(cached["versions"]) or any(
                    memory_version(item) != cached["versions"][mid] for mid, item in records.items()):
                self.store.cache(job, "decisions", None)
                job["decisions"] = None
            else:
                return records, parse_decisions({"decisions": cached["items"]}, len(candidates), set(records))
        records = self._related(candidates)
        decisions = extractor.relate(
            candidates, tuple(records.values()),
            before_send=lambda: self._check_relation_context(records),
        ) if records else [
            {"index": i, "action": "new", "target_id": None, "reason": "No related memory retrieved."}
            for i in range(len(candidates))
        ]
        self.store.cache(job, "decisions", {"items": decisions,
                                          "versions": {mid: memory_version(item) for mid, item in records.items()}})
        return records, decisions

    def _check_relation_context(self, records: dict[str, LongTermMemory]) -> None:
        for expected in records.values():
            current = self.backend.get(expected.id)
            if (current.user_id != expected.user_id or current.scope is not expected.scope
                    or current.persona_id != expected.persona_id
                    or not self.can_send(current, "processing")
                    or memory_version(current) != memory_version(expected)):
                raise JournalMemoryError("journal_target_changed")

    def _load_targets(self, versions: dict[str, str]) -> dict[str, LongTermMemory]:
        records = {}
        for mid in versions:
            try:
                item = self.backend.get(mid)
            except MemoryBackendError as exc:
                if exc.code != "memory_not_found":
                    raise
                continue
            if item.status is MemoryStatus.ACTIVE and self.can_send(item, "processing"):
                records[mid] = item
        return records

    def _related(self, candidates: Sequence[JournalCandidate]) -> dict[str, LongTermMemory]:
        found: dict[str, LongTermMemory] = {}
        for candidate in candidates:
            rows = self.backend.search(candidate.content, user_id=self.policy.user_id,
                                       scope=MemoryScope.SHARED, limit=8)
            for item in rows:
                if (item.user_id == self.policy.user_id and item.scope is MemoryScope.SHARED
                        and item.status is MemoryStatus.ACTIVE and len(item.content) <= 600
                        and self.can_send(item, "processing")):
                    found[item.id] = item
            exact = self.store.rows("SELECT id FROM records WHERE user_id=? AND content_hash=? AND state<>'deleted'",
                                    (self.policy.user_id, content_key(candidate.content)))
            for row in exact[:4]:
                try:
                    item = self.backend.get(row["id"])
                except MemoryBackendError as exc:
                    if exc.code != "memory_not_found":
                        raise
                    continue
                if (item.user_id == self.policy.user_id and item.scope is MemoryScope.SHARED
                        and item.status is MemoryStatus.ACTIVE and self.can_send(item, "processing")):
                    found[item.id] = item
        return dict(list(found.items())[:12])

    def _apply(self, write: _Write, guard: Callable[[], None]) -> None:
        candidate, evidence = write.candidate, write.evidence
        operation = self.store.operation(write.operation_id)
        if operation and operation["status"] == "applied":
            return
        if self.store.suppressed(evidence_id=evidence.id, content_hash=content_key(candidate.content)):
            self.store.begin_operation(write.operation_id, evidence.id, "suppressed")
            self.store.finish_operation(write.operation_id, "")
            return
        action = write.decision["action"]
        target = self._validate_target(write.target)
        if target and action == "duplicate":
            self._register_target(target)
            self.store.register(self.store.record(target.id), evidence.id)  # type: ignore[arg-type]
            self.store.begin_operation(write.operation_id, evidence.id, action)
            self.store.finish_operation(write.operation_id, target.id)
            return
        if target and action in {"enrich", "state_change"}:
            target_date = _fact_date(target)
            if target.metadata.get("reviewed_at") or (action == "state_change" and (
                    not candidate.valid_from or not target_date or candidate.valid_from == target_date)):
                action = "conflict"
        self.store.begin_operation(write.operation_id, evidence.id, action)
        added = self._find_operation(write)
        if added is None:
            added = self._add(write, action)
        elif (added.metadata.get("relation_target") != (target.id if target else None)
              or added.metadata.get("relation_action") != action):
            added = self.backend.update(added.id, metadata=dict(
                added.metadata, relation_action=action, relation_target=target.id if target else None,
                relation_reason=write.decision["reason"]))
        guard()
        self.current_evidence(evidence.id, expected_version=evidence.version)
        self._validate_target(write.target)
        self._register_write(write, added, action)
        self.store.finish_operation(write.operation_id, added.id)

    def _validate_target(self, target: Optional[LongTermMemory]) -> Optional[LongTermMemory]:
        if target is None:
            return None
        current = self.backend.get(target.id)
        if current.user_id != self.policy.user_id or current.scope is not MemoryScope.SHARED:
            raise JournalMemoryError("journal_relation_scope_violation")
        if current.status is not MemoryStatus.ACTIVE or memory_version(current) != memory_version(target):
            raise JournalMemoryError("journal_target_changed")
        if not self.can_send(current, "processing"):
            raise JournalMemoryError("journal_target_changed")
        if self.store.suppressed(memory_id=current.id):
            raise JournalMemoryError("journal_target_forgotten")
        return current

    def _find_operation(self, write: _Write) -> Optional[LongTermMemory]:
        lookup = getattr(self.backend, "find_operation", None)
        if lookup:
            return lookup(write.operation_id, user_id=self.policy.user_id, content=write.candidate.content)
        rows = self.backend.list_memories(user_id=self.policy.user_id, include_archived=True, limit=1000)
        return next((item for item in rows if item.metadata.get("journal_operation_id") == write.operation_id), None)

    def _add(self, write: _Write, action: str) -> LongTermMemory:
        evidence, candidate = write.evidence, write.candidate
        metadata = {
            "journal_managed": True, "journal_operation_id": write.operation_id,
            "journal_kind": candidate.kind, "certainty": candidate.certainty,
            "privacy": "local" if candidate.certainty == "inferred" else "cloud",
            "valid_from": candidate.valid_from, "source_created_at": evidence.observed_at,
            "source_type": "journal" if evidence.kind == "daily" else "journal-summary",
            "source_id": "riji/" + evidence.path[:-3], "evidence_id": evidence.id,
            "source_version": evidence.version, "source_section": evidence.section,
            "source_line": evidence.line, "scope": "shared", "status": "active",
            "relation_action": action, "relation_target": write.target.id if write.target else None,
            "relation_reason": ("用户修订或时间依据不足，保留冲突。" if action != write.decision["action"] else "") + write.decision["reason"],
            "captured_at": utc_now(),
        }
        explicit = getattr(self.backend, "add_explicit", None)
        result = explicit(candidate.content, user_id=self.policy.user_id, metadata=metadata) if explicit else (
            self.backend.add(candidate.content, user_id=self.policy.user_id, scope=MemoryScope.SHARED,
                             persona_id=None, metadata=metadata))
        if len(result) != 1 or result[0].user_id != self.policy.user_id:
            raise JournalMemoryError("journal_invalid_backend_write")
        return result[0]

    def _register_write(self, write: _Write, added: LongTermMemory, action: str) -> None:
        series = added.id
        if write.target:
            self._register_target(write.target)
            if action in {"state_change", "conflict"}:
                series = self.store.record(write.target.id)["series"]  # type: ignore[index]
            self.store.link(added.id, write.target.id, action)
        record = {"id": added.id, "user_id": self.policy.user_id,
                  "content_hash": content_key(added.content), "kind": write.candidate.kind,
                  "valid_from": write.candidate.valid_from, "series": series,
                  "state": "conflict" if action == "conflict" else "current",
                  "protected": 0, "created_at": utc_now()}
        self.store.register(record, write.evidence.id)
        if action == "state_change":
            self.store.execute("UPDATE records SET state='historical' WHERE series=? AND state='current' "
                               "AND valid_from<(SELECT MAX(valid_from) FROM records WHERE series=? "
                               "AND state IN ('current','historical'))", (series, series))
        if action == "conflict" and write.target:
            self.store.execute("UPDATE records SET state='conflict' WHERE id=?", (write.target.id,))

    def _register_target(self, target: LongTermMemory) -> None:
        if self.store.record(target.id):
            return
        self.store.register({"id": target.id, "user_id": target.user_id,
                             "content_hash": content_key(target.content), "kind": "legacy",
                             "valid_from": _fact_date(target), "series": target.id,
                             "state": "current", "protected": int(bool(target.metadata.get("reviewed_at"))),
                             "created_at": utc_now()}, "")
        if not target.metadata.get("journal_managed") and target.metadata.get("source_request_id"):
            self.store.execute("INSERT OR IGNORE INTO native_supports VALUES (?,?,?,?,?)",
                               (target.id, target.user_id, target.metadata["source_request_id"],
                                target.metadata.get("source_id", "conversation/unknown"), target.metadata.get("source_created_at")))

    def current_evidence(self, evidence_id: str, *, expected_version: str | None = None) -> JournalEvidence:
        if not self.policy.enabled or self.store.get_control("scope") != self.policy.scope_id:
            raise JournalMemoryError("journal_source_changed")
        stored = self.store.evidence(evidence_id)
        if not stored or not stored["active"]:
            raise JournalMemoryError("journal_source_changed")
        payload = json.loads(stored["payload"])
        source = read_source(self.policy.root / payload["path"], self.policy)
        if expected_version and source.version != expected_version:
            raise JournalMemoryError("journal_source_changed")
        found = next((item for item in source.evidence if item.id == evidence_id), None)
        if found is None:
            raise JournalMemoryError("journal_source_changed")
        return found

    def can_send(self, item: LongTermMemory, purpose: str = "recall") -> bool:
        if item.metadata.get("privacy", "cloud") != "cloud" or not self.is_valid(item):
            return False
        if not item.metadata.get("journal_managed"):
            return True
        allowed = (self.privacy.allows("history") or self.privacy.allows("incremental")) if purpose == "processing" else self.privacy.allows(purpose)
        if not allowed:
            return False
        # A manual correction or a second source must not widen a restricted source.
        for ref in self.store.rows("SELECT evidence_id FROM supports WHERE memory_id=?", (item.id,)):
            try:
                self.current_evidence(ref["evidence_id"])
            except JournalMemoryError:
                return False
        return True

    def is_valid(self, item: LongTermMemory) -> bool:
        if item.user_id != self.policy.user_id:
            return not item.metadata.get("journal_managed")
        if self.store.get_control("restore_in_progress"):
            return False
        if self.store.suppressed(memory_id=item.id, content_hash=content_key(item.content)):
            return False
        record = self.store.record(item.id)
        if record and record["state"] == "deleted":
            return False
        if not item.metadata.get("journal_managed") or (record and record["protected"]):
            return True
        if self.store.rows("SELECT 1 FROM native_supports WHERE memory_id=? AND user_id=?", (item.id, item.user_id)):
            return True
        refs = self.store.rows("SELECT evidence_id FROM supports WHERE memory_id=?", (item.id,))
        for ref in refs:
            try:
                self.current_evidence(ref["evidence_id"])
                return True
            except JournalMemoryError:
                continue
        return False


def _fact_date(item: LongTermMemory) -> str | None:
    value = item.metadata.get("valid_from")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None
