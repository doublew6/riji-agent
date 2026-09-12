"""Idempotent migration from legacy confirmed SQLite memories to Mem0."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from riji_agent.memory.backend import LongTermMemoryBackend, MemoryBackendError
from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.models import MemoryScope, MemoryStatus, NewMemoryChange
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.service import MemoryService
from riji_agent.memory.store import MemoryStore

_CURRENT_PERSONA = "current_persona"


@dataclass(frozen=True)
class MigrationItem:
    user_id: str
    legacy_id: str
    content: str
    source_type: str


@dataclass(frozen=True)
class MigrationResult:
    discovered: int
    migrated: int
    skipped: int
    backup_path: Path | None


class MemoryMigrator:
    def __init__(
        self,
        legacy_store: MemoryStore,
        backend: LongTermMemoryBackend,
        operations: MemoryOperationsStore,
        service: MemoryService,
    ) -> None:
        self._legacy = legacy_store
        self._backend = backend
        self._operations = operations
        self._service = service

    def inspect(self) -> Sequence[MigrationItem]:
        users = set(self._legacy.list_confirmed_users())
        users.update(self._legacy.list_preference_users())
        items = []
        for user_id in sorted(users):
            items.extend(self._memory_items(user_id))
            items.extend(self._preference_items(user_id))
        return tuple(items)

    def run(self, *, apply: bool) -> MigrationResult:
        items = self.inspect()
        existing = self._existing_ids(item.user_id for item in items)
        pending = [item for item in items if item.legacy_id not in existing]
        if not apply:
            return MigrationResult(len(items), 0, len(items) - len(pending), None)
        if not pending:
            return MigrationResult(len(items), 0, len(items), None)
        backup = self._backup()
        migrated = 0
        for item in pending:
            self._migrate_item(item)
            migrated += 1
        self._service.request_snapshot()
        return MigrationResult(len(items), migrated, len(items) - migrated, backup)

    def _memory_items(self, user_id: str) -> Sequence[MigrationItem]:
        return tuple(
            MigrationItem(
                user_id,
                f"confirmed:{memory.id}",
                memory.content,
                "legacy-confirmed-memory",
            )
            for memory in self._legacy.list_confirmed_memories(user_id)
            if not contains_credentials(memory.content)
        )

    def _preference_items(self, user_id: str) -> Sequence[MigrationItem]:
        return tuple(
            MigrationItem(
                user_id,
                f"preference:{key}",
                f"User preference — {key}: {value}",
                "legacy-preference",
            )
            for key, value in self._legacy.get_preferences(user_id).items()
            if key != _CURRENT_PERSONA
            and not contains_credentials(f"{key}: {value}")
        )

    def _existing_ids(self, users: Iterable[str]) -> set[str]:
        ids: set[str] = set()
        for user_id in sorted(set(users)):
            records = self._backend.list_memories(
                user_id=user_id, include_archived=True, limit=1000
            )
            ids.update(
                str(item.metadata["legacy_id"])
                for item in records
                if item.metadata.get("legacy_id")
            )
        return ids

    def _migrate_item(self, item: MigrationItem) -> None:
        metadata = {
            "legacy_id": item.legacy_id,
            "source_type": item.source_type,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "scope": MemoryScope.SHARED.value,
            "status": MemoryStatus.ACTIVE.value,
        }
        added = self._backend.add(
            item.content,
            user_id=item.user_id,
            scope=MemoryScope.SHARED,
            persona_id=None,
            metadata=metadata,
        )
        if not added:
            raise MemoryBackendError("mem0_add_empty")
        for memory in added:
            self._operations.record_change(
                NewMemoryChange(
                    memory_id=memory.id,
                    user_id=item.user_id,
                    persona_id=None,
                    scope=MemoryScope.SHARED,
                    action="MIGRATE",
                    before=None,
                    after=item.content,
                    source_request_id=item.legacy_id,
                )
            )

    def _backup(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        source = self._legacy.database_path
        backup = source.with_name(f"{source.name}.backup-{stamp}")
        return self._legacy.backup_to(backup)
