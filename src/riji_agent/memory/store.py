"""SQLite persistence for memory and persona-private sessions.

Sharing/isolation rules (architecture §3.2):
- confirmed memories and preferences are shared across a user's personas;
- memory candidates are persona-private until confirmed;
- session history is persona-private (keyed by user + persona + chat).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from riji_agent.memory.models import (
    CandidateStatus,
    ConfirmedMemory,
    MemoryCandidate,
    SessionMessage,
    session_key,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmed_memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    content   TEXT NOT NULL,
    source_candidate_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_candidates (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    content   TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS session_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Access is serialized by the gateway lock; allow use across FastAPI
        # threadpool threads.
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------- candidates (private)

    def add_candidate(self, user_id: str, persona_id: str, content: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO memory_candidates (user_id, persona_id, content, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, persona_id, content, CandidateStatus.PENDING.value, _now()),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_candidates(
        self, user_id: str, persona_id: str, *, status: Optional[CandidateStatus] = None
    ) -> List[MemoryCandidate]:
        sql = "SELECT * FROM memory_candidates WHERE user_id = ? AND persona_id = ?"
        params: List[object] = [user_id, persona_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY id"
        return [self._to_candidate(row) for row in self._conn.execute(sql, params)]

    def confirm_candidate(self, candidate_id: int) -> int:
        """Promote a candidate into shared confirmed memory; returns memory id."""
        row = self._conn.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such candidate: {candidate_id}")
        cursor = self._conn.execute(
            "INSERT INTO confirmed_memories (user_id, content, source_candidate_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (row["user_id"], row["content"], candidate_id, _now()),
        )
        self._conn.execute(
            "UPDATE memory_candidates SET status = ? WHERE id = ?",
            (CandidateStatus.CONFIRMED.value, candidate_id),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def reject_candidate(self, candidate_id: int) -> None:
        self._conn.execute(
            "UPDATE memory_candidates SET status = ? WHERE id = ?",
            (CandidateStatus.REJECTED.value, candidate_id),
        )
        self._conn.commit()

    # ------------------------------------------------------ confirmed (shared)

    def list_confirmed_memories(self, user_id: str) -> List[ConfirmedMemory]:
        rows = self._conn.execute(
            "SELECT * FROM confirmed_memories WHERE user_id = ? ORDER BY id", (user_id,)
        )
        return [
            ConfirmedMemory(
                id=row["id"],
                user_id=row["user_id"],
                content=row["content"],
                source_candidate_id=row["source_candidate_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ---------------------------------------------------- preferences (shared)

    def set_preference(self, user_id: str, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO preferences (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, value),
        )
        self._conn.commit()

    def get_preferences(self, user_id: str) -> Dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM preferences WHERE user_id = ? ORDER BY key", (user_id,)
        )
        return {row["key"]: row["value"] for row in rows}

    # ----------------------------------------------------- sessions (private)

    def append_message(
        self, user_id: str, persona_id: str, chat_id: str, role: str, content: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO session_messages (session_key, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_key(user_id, persona_id, chat_id), role, content, _now()),
        )
        self._conn.commit()

    def get_session_history(
        self, user_id: str, persona_id: str, chat_id: str
    ) -> List[SessionMessage]:
        rows = self._conn.execute(
            "SELECT role, content, created_at FROM session_messages "
            "WHERE session_key = ? ORDER BY id",
            (session_key(user_id, persona_id, chat_id),),
        )
        return [
            SessionMessage(role=row["role"], content=row["content"], created_at=row["created_at"])
            for row in rows
        ]

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _to_candidate(row: sqlite3.Row) -> MemoryCandidate:
        return MemoryCandidate(
            id=row["id"],
            user_id=row["user_id"],
            persona_id=row["persona_id"],
            content=row["content"],
            status=CandidateStatus(row["status"]),
            created_at=row["created_at"],
        )
