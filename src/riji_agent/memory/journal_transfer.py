"""Portable local memory bundles with suppression-first resumable restoration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.journal_backend import JournalEvidenceBackend
from riji_agent.memory.journal_types import JournalMemoryError, content_key, fingerprint, utc_now
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.memory.organization import memory_fingerprint

_FORMAT = "riji-journal-memory/v1"
_TABLES = ("sources", "evidence", "records", "supports", "relations", "operations", "content_aliases", "suppression", "native_supports", "budgets")
_MAX_BYTES = 32 * 1024 * 1024


class JournalMemoryTransfer:
    def __init__(self, backend: JournalEvidenceBackend) -> None:
        self.backend, self.engine = backend, backend.engine
        self.store = self.engine.store
        self.store.execute("CREATE TABLE IF NOT EXISTS restore_items (bundle_id TEXT, old_id TEXT, new_id TEXT, "
                           "PRIMARY KEY(bundle_id,old_id))")

    def export(self, path: Path) -> dict[str, Any]:
        _check_path(path, self.engine.policy.root)
        epoch = self.store.get_control("catalog_epoch")
        records = self.backend.export_memories(user_id=self.engine.policy.user_id)
        tables = {name: self.store.rows(f"SELECT * FROM {name}") for name in _TABLES}
        for row in tables["evidence"]:
            row.update(extracted=None, decisions=None, token=None, lease_until=None)
            payload = json.loads(row["payload"])
            payload.pop("text", None)
            row["payload"] = json.dumps(payload, ensure_ascii=False)
            if row["status"] == "processing":
                row["status"] = "pending"
        current = self.backend.export_memories(user_id=self.engine.policy.user_id)
        if epoch != self.store.get_control("catalog_epoch") or memory_fingerprint(current) != memory_fingerprint(records):
            raise JournalMemoryError("memory_export_changed_retry")
        policy = self.engine.policy
        payload = {"format": _FORMAT, "user_id": policy.user_id, "created_at": utc_now(),
                   "scope": {"sections": list(policy.sections), "date_from": policy.date_from, "date_to": policy.date_to},
                   "memories": [_serialize(item) for item in records], "tables": tables}
        _write_bundle(path, payload)
        return {"memories": len(records), "sources": len(tables["sources"]), "format": _FORMAT}

    def restore(self, path: Path, *, apply: bool = False) -> dict[str, Any]:
        _check_path(path, self.engine.policy.root)
        payload = _read_bundle(path, self.engine.policy.user_id)
        bundle_id = fingerprint(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        self._validate_tables(payload)
        memories = [_deserialize(row, self.engine.policy.user_id) for row in payload["memories"]]
        if not apply:
            return {"mode": "dry-run", "memories": len(memories), "sources": len(payload["tables"]["sources"]),
                    "scope": payload["scope"], "requires_empty_target": True}
        self._begin_restore(bundle_id)
        self._replay_suppression(payload["tables"]["suppression"])
        for row in payload["tables"]["budgets"]:
            self.store.execute("INSERT INTO budgets VALUES (?,?) ON CONFLICT(key) DO UPDATE SET chars=MAX(chars,excluded.chars)",
                               (row["key"], row["chars"]))
        mapping = self._restore_memories(memories, bundle_id)
        self._restore_links(memories, mapping)
        self._restore_tables(payload["tables"], mapping)
        self.engine.scan()
        self.store.set_control("restore_in_progress", "")
        self.engine.notify_change()
        return {"mode": "applied", "restored": len(mapping), "suppressed": len(memories) - len(mapping), "paused": True}

    def _begin_restore(self, bundle_id: str) -> None:
        pending = self.store.get_control("restore_in_progress")
        if pending and pending != bundle_id:
            raise JournalMemoryError("another_memory_restore_pending")
        rows = self.engine.backend.export_memories(user_id=self.engine.policy.user_id) if hasattr(
            self.engine.backend, "export_memories") else self.engine.backend.list_memories(
                user_id=self.engine.policy.user_id, include_archived=True, limit=1000)
        if any(row.metadata.get("restore_bundle") != bundle_id for row in rows):
            raise JournalMemoryError("memory_restore_requires_empty_target")
        if self.store.rows("SELECT id FROM evidence WHERE status='processing' AND lease_until>?", (utc_now(),)):
            raise JournalMemoryError("memory_restore_wait_for_running_jobs")
        if not pending:
            self.store.backup(self.store.path.with_name(f"journal-before-restore-{bundle_id[:12]}.sqlite3"))
        self.store.set_control("paused", "1")
        self.store.set_control("restore_in_progress", bundle_id)

    def _replay_suppression(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.store.execute("INSERT OR IGNORE INTO suppression VALUES (?,?,?)",
                               (row["kind"], row["value"], row["created_at"]))

    def _restore_memories(self, records: list[LongTermMemory], bundle_id: str) -> dict[str, str]:
        mapping = {}
        for item in records:
            if self.store.suppressed(memory_id=item.id, content_hash=content_key(item.content)):
                continue
            operation_id = fingerprint(bundle_id + ":" + item.id)
            metadata = dict(item.metadata, journal_operation_id=operation_id, restore_bundle=bundle_id,
                            scope=item.scope.value, status=item.status.value, persona_id=item.persona_id)
            added = self._restore_one(item, metadata)
            mapping[item.id] = added.id
            self.store.execute("INSERT OR REPLACE INTO restore_items VALUES (?,?,?)", (bundle_id, item.id, added.id))
        return mapping

    def _restore_one(self, item: LongTermMemory, metadata: dict[str, Any]) -> LongTermMemory:
        raw = self.engine.backend
        explicit = getattr(raw, "add_explicit", None)
        if explicit:
            result = explicit(item.content, user_id=item.user_id, metadata=metadata)
        else:
            existing = raw.list_memories(user_id=item.user_id, include_archived=True, limit=1000)
            result = [row for row in existing if row.metadata.get("journal_operation_id") == metadata["journal_operation_id"]]
            if not result:
                result = raw.add(item.content, user_id=item.user_id, scope=item.scope,
                                 persona_id=item.persona_id, metadata=metadata)
        if len(result) != 1 or result[0].user_id != item.user_id or result[0].scope is not item.scope:
            raise JournalMemoryError("invalid_memory_restore_result")
        return result[0]

    def _restore_links(self, records: list[LongTermMemory], mapping: dict[str, str]) -> None:
        for original in records:
            if original.id not in mapping:
                continue
            current = self.engine.backend.get(mapping[original.id])
            metadata = dict(current.metadata)
            for field in ("relation_target", "journal_series"):
                if field in metadata:
                    metadata[field] = mapping.get(original.metadata.get(field))
            if original.metadata.get("relation_target") and not metadata.get("relation_target"):
                metadata["relation_reason"] = "Related memory was not restored."
            self.engine.backend.update(current.id, metadata=metadata)

    def _validate_tables(self, payload: dict[str, Any]) -> None:
        tables = payload["tables"]
        if set(tables) != set(_TABLES):
            raise JournalMemoryError("invalid_memory_bundle_tables")
        memory_ids = {item["id"] for item in payload["memories"]}
        for table in _TABLES:
            if not isinstance(tables[table], list):
                raise JournalMemoryError("invalid_memory_bundle_tables")
            columns = {row["name"] for row in self.store.rows(f"PRAGMA table_info({table})")}
            for row in tables[table]:
                if not isinstance(row, dict) or set(row) != columns:
                    raise JournalMemoryError("invalid_memory_bundle_columns")
                if any(value is not None and type(value) not in (str, int) for value in row.values()):
                    raise JournalMemoryError("invalid_memory_bundle_value")
        for row in tables["sources"]:
            _validate_source(row["path"])
        for row in tables["evidence"]:
            try:
                evidence = json.loads(row["payload"])
                if not isinstance(evidence, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise JournalMemoryError("invalid_memory_bundle_evidence") from None
            _validate_source(evidence["path"])
            if "text" in evidence or row["extracted"] is not None or row["decisions"] is not None:
                raise JournalMemoryError("memory_bundle_contains_raw_payload")
        for row in tables["records"]:
            if row["user_id"] != payload["user_id"] or row["protected"] not in (0, 1):
                raise JournalMemoryError("invalid_memory_bundle_owner")
        if any(row["user_id"] != payload["user_id"] for row in tables["native_supports"]):
            raise JournalMemoryError("invalid_memory_bundle_owner")
        for row in tables["suppression"]:
            if row["kind"] not in {"memory", "content", "evidence"} or not isinstance(row["value"], str):
                raise JournalMemoryError("invalid_memory_bundle_suppression")
        if len(memory_ids) != len(payload["memories"]):
            raise JournalMemoryError("duplicate_memory_bundle_ids")
        if any(not isinstance(row["key"], str) or type(row["chars"]) is not int or row["chars"] < 0
               for row in tables["budgets"]):
            raise JournalMemoryError("invalid_memory_bundle_budget")
        _validate_references(tables)

    def _restore_tables(self, tables: dict[str, Any], mapping: dict[str, str]) -> None:
        for table in ("sources", "evidence"):
            for original in tables[table]:
                row = dict(original)
                if table == "evidence":
                    row.update(active=0, token=None, lease_until=None, extracted=None, decisions=None)
                    if self.store.suppressed(evidence_id=row["id"]):
                        row["status"] = "suppressed"
                self._insert(table, row)
        for table in ("records", "supports", "relations", "operations", "content_aliases", "native_supports"):
            for original in tables[table]:
                row = _remap(table, original, mapping)
                if row is not None:
                    self._insert(table, row)

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        columns = list(row)
        self.store.execute(f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           tuple(row[key] for key in columns))


def _remap(table: str, original: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any] | None:
    row = dict(original)
    fields = {"records": ("id", "series"), "supports": ("memory_id",), "relations": ("source_id", "target_id"),
              "operations": ("memory_id",), "content_aliases": ("memory_id",), "native_supports": ("memory_id",)}[table]
    for field in fields:
        if field == "series":
            row[field] = mapping.get(row[field], row["id"])
        elif row[field] not in mapping:
            return None
        else:
            row[field] = mapping[row[field]]
    return row


def _validate_references(tables: dict[str, Any]) -> None:
    sources = {row["id"] for row in tables["sources"]}
    evidence = {row["id"] for row in tables["evidence"]}
    records = {row["id"] for row in tables["records"]}
    for row in tables["evidence"]:
        if row["source_id"] not in sources or row["status"] not in {
                "pending", "processing", "retry", "budget", "source_budget", "failed", "succeeded", "suppressed"}:
            raise JournalMemoryError("invalid_memory_bundle_evidence")
    for row in tables["supports"]:
        if row["memory_id"] not in records or row["evidence_id"] not in evidence:
            raise JournalMemoryError("invalid_memory_bundle_support")
    for row in tables["relations"]:
        if row["source_id"] not in records or row["target_id"] not in records:
            raise JournalMemoryError("invalid_memory_bundle_relation")
    for row in tables["records"]:
        if row["state"] not in {"current", "historical", "conflict", "deleted"} or row["series"] not in records:
            raise JournalMemoryError("invalid_memory_bundle_state")


def _serialize(item: LongTermMemory) -> dict[str, Any]:
    row = asdict(item)
    row.update(scope=item.scope.value, status=item.status.value)
    return row


def _deserialize(row: dict[str, Any], user_id: str) -> LongTermMemory:
    try:
        item = LongTermMemory(**dict(row, scope=MemoryScope(row["scope"]), status=MemoryStatus(row["status"])))
        if (item.user_id != user_id or not item.id or not isinstance(item.content, str)
                or not 1 <= len(item.content) <= 2000 or contains_credentials(item.content)
                or not isinstance(item.metadata, dict) or len(json.dumps(item.metadata)) > 12000
                or (item.scope is MemoryScope.PERSONA) != bool(item.persona_id)):
            raise ValueError
        return item
    except (KeyError, TypeError, ValueError):
        raise JournalMemoryError("invalid_memory_bundle_record") from None


def _read_bundle(path: Path, user_id: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_BYTES:
            raise ValueError
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or set(payload) != {"format", "user_id", "created_at", "scope", "memories", "tables"}
                or payload["format"] != _FORMAT or payload["user_id"] != user_id
                or not isinstance(payload["memories"], list) or not isinstance(payload["tables"], dict)):
            raise ValueError
        return payload
    except (OSError, UnicodeError, ValueError):
        raise JournalMemoryError("invalid_memory_bundle") from None


def _validate_source(value: Any) -> None:
    if not isinstance(value, str):
        raise JournalMemoryError("invalid_memory_bundle_source")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or not path.parts
            or path.parts[0] not in {"daily", "weekly", "monthly"} or path.suffix.lower() != ".md"):
        raise JournalMemoryError("invalid_memory_bundle_source")


def _check_path(path: Path, root: Path) -> None:
    resolved = path.expanduser().resolve()
    if resolved == root or root in resolved.parents or path.is_symlink():
        raise JournalMemoryError("memory_bundle_must_be_outside_journal")


def _write_bundle(path: Path, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(data.encode("utf-8")) > _MAX_BYTES:
        raise JournalMemoryError("memory_bundle_too_large")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
