"""Idempotency log: a Feishu event is processed at most once.

A duplicate event returns the previously stored reply instead of acting again.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_events (
    event_id   TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    reply      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ProcessedEvent:
    persona_id: str
    reply: str


class EventLog:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Access is serialized by the gateway lock; allow use across FastAPI
        # threadpool threads.
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, event_id: str) -> Optional[ProcessedEvent]:
        row = self._conn.execute(
            "SELECT persona_id, reply FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return ProcessedEvent(persona_id=row["persona_id"], reply=row["reply"])

    def record(self, event_id: str, persona_id: str, reply: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, persona_id, reply, created_at) "
            "VALUES (?, ?, ?, ?)",
            (event_id, persona_id, reply, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
