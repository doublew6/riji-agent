"""Auditable, repeatable capture of retained user-authored conversations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from riji_agent.memory.capture import should_auto_capture
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.store import MemoryStore


@dataclass(frozen=True)
class BackfillResult:
    discovered: int
    eligible: int
    skipped: int
    already_queued: int
    enqueued: int
    backup_path: Path | None = None


class MemoryBackfill:
    def __init__(
        self, store: MemoryStore, operations: MemoryOperationsStore, user_ids: set[str]
    ) -> None:
        self._store = store
        self._operations = operations
        self._user_ids = user_ids

    def run(self, *, apply: bool) -> BackfillResult:
        known = self._operations.captured_message_ids()
        discovered = eligible = skipped = already_queued = enqueued = 0
        backup_path = None
        for message in self._store.iter_user_messages(self._user_ids):
            discovered += 1
            if not should_auto_capture(message.content):
                skipped += 1
                continue
            eligible += 1
            if message.id in known:
                already_queued += 1
                continue
            if not apply:
                continue
            if backup_path is None:
                backup_path = self._backup()
            self._operations.enqueue(
                source_request_id=f"session-message:{message.id}",
                user_id=message.user_id,
                persona_id=message.persona_id,
                session_id=message.session_id,
                content=message.content,
                source_message_id=message.id,
                source_created_at=message.created_at,
            )
            enqueued += 1
        return BackfillResult(discovered, eligible, skipped, already_queued, enqueued, backup_path)

    def _backup(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        source = self._store.database_path
        destination = source.with_name(f"{source.name}.before-backfill-{stamp}")
        path = self._store.backup_to(destination)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise OSError("memory backup verification failed")
        return path
