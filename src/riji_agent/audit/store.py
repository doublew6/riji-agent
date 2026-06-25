"""SQLite persistence for audit events (metadata only)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from riji_agent.audit.models import AuditEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id     TEXT NOT NULL,
    persona_id     TEXT NOT NULL,
    feishu_user_id TEXT NOT NULL,
    tool           TEXT NOT NULL,
    ok             INTEGER NOT NULL,
    error          TEXT,
    source_ids     TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""


class AuditStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(
        self,
        *,
        request_id: str,
        persona_id: str,
        feishu_user_id: str,
        tool: str,
        ok: bool,
        error: Optional[str],
        source_ids,
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_events (request_id, persona_id, feishu_user_id, tool, ok, "
            "error, source_ids, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                persona_id,
                feishu_user_id,
                tool,
                int(ok),
                error,
                json.dumps(list(source_ids), ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def list_for_request(self, request_id: str) -> List[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM audit_events WHERE request_id = ? ORDER BY id", (request_id,)
        )
        return [self._to_event(row) for row in rows]

    def all(self) -> List[AuditEvent]:
        return [self._to_event(row) for row in self._conn.execute(
            "SELECT * FROM audit_events ORDER BY id"
        )]

    def all_source_ids(self) -> List[str]:
        ids: List[str] = []
        for row in self._conn.execute("SELECT source_ids FROM audit_events"):
            ids.extend(json.loads(row["source_ids"]))
        return ids

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"])

    @staticmethod
    def _to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            request_id=row["request_id"],
            persona_id=row["persona_id"],
            feishu_user_id=row["feishu_user_id"],
            tool=row["tool"],
            ok=bool(row["ok"]),
            error=row["error"],
            source_ids=tuple(json.loads(row["source_ids"])),
            created_at=row["created_at"],
        )
