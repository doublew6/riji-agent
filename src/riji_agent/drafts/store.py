"""SQLite persistence for drafts and their confirmation tokens."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date as Date
from pathlib import Path
from typing import Optional

from riji_agent.drafts.models import Draft, DraftOperation, DraftStatus
from riji_agent.journal.content import DiscussionProvenance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    draft_id   TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    target_date TEXT NOT NULL,
    operations TEXT NOT NULL,
    token      TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_id  TEXT,
    after_hash TEXT
);
CREATE TABLE IF NOT EXISTS draft_preview_bindings (
    draft_id TEXT PRIMARY KEY,
    binding TEXT NOT NULL,
    display_event_id TEXT NOT NULL
);
"""


class DraftStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Access is serialized by the gateway lock; allow cross-thread use.
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save(self, draft: Draft) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO drafts (draft_id, user_id, session_id, persona_id, "
            "target_date, operations, token, status, created_at, expires_at, source_id, after_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft.draft_id,
                draft.user_id,
                draft.session_id,
                draft.persona_id,
                draft.target_date.isoformat(),
                json.dumps(
                    [asdict(o) for o in draft.operations],
                    ensure_ascii=False,
                ),
                draft.token,
                draft.status.value,
                draft.created_at,
                draft.expires_at,
                draft.source_id,
                draft.after_hash,
            ),
        )
        self._conn.commit()

    def get(self, draft_id: str) -> Optional[Draft]:
        row = self._conn.execute(
            "SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return self._to_draft(row) if row else None

    def claim_for_commit(self, draft_id: str, *, binding: Optional[str] = None) -> bool:
        """Atomically move a draft from AWAITING to COMMITTING.

        Returns ``True`` only for the caller that won the claim. This single
        conditional UPDATE is the cross-process mutex for the commit: SQLite
        serializes writers, so among several concurrent confirmations only one
        matches the still-awaiting row; the others affect zero rows and lose,
        which closes the check-then-act race without relying on a process lock.
        """
        cursor = self._conn.execute(
            "UPDATE drafts SET status = ? WHERE draft_id = ? AND status = ? "
            "AND ((? IS NULL AND NOT EXISTS (SELECT 1 FROM draft_preview_bindings b "
            "WHERE b.draft_id=drafts.draft_id)) OR EXISTS (SELECT 1 FROM draft_preview_bindings b "
            "WHERE b.draft_id=drafts.draft_id AND b.binding=?))",
            (DraftStatus.COMMITTING.value, draft_id, DraftStatus.AWAITING.value, binding, binding),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def bind_preview(self, draft_id: str, binding: str, display_event_id: str) -> None:
        self._conn.execute(
            "INSERT INTO draft_preview_bindings VALUES (?, ?, ?) "
            "ON CONFLICT(draft_id) DO UPDATE SET binding=excluded.binding, display_event_id=excluded.display_event_id",
            (draft_id, binding, display_event_id),
        )
        self._conn.commit()

    def preview_binding(self, draft_id: str) -> Optional[str]:
        row = self._conn.execute("SELECT binding FROM draft_preview_bindings WHERE draft_id=?", (draft_id,)).fetchone()
        return row[0] if row else None

    def get_latest_awaiting_for_session(self, session_id: str) -> Optional[Draft]:
        row = self._conn.execute(
            "SELECT * FROM drafts WHERE session_id = ? AND status = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id, DraftStatus.AWAITING.value),
        ).fetchone()
        return self._to_draft(row) if row else None

    def get_latest_for_session(self, session_id: str) -> Optional[Draft]:
        row = self._conn.execute(
            "SELECT * FROM drafts WHERE session_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._to_draft(row) if row else None

    def get_latest_committed_for_session(self, session_id: str) -> Optional[Draft]:
        row = self._conn.execute(
            "SELECT * FROM drafts WHERE session_id = ? AND status = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id, DraftStatus.COMMITTED.value),
        ).fetchone()
        return self._to_draft(row) if row else None

    @staticmethod
    def _to_draft(row: sqlite3.Row) -> Draft:
        operations = tuple(
            _operation(item)
            for item in json.loads(row["operations"])
        )
        return Draft(
            draft_id=row["draft_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            persona_id=row["persona_id"],
            target_date=Date.fromisoformat(row["target_date"]),
            operations=operations,
            token=row["token"],
            status=DraftStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            source_id=row["source_id"],
            after_hash=row["after_hash"],
        )


def _operation(item: dict | list) -> DraftOperation:
    if isinstance(item, list):
        return DraftOperation(section=item[0], content=item[1])
    data = dict(item)
    if data.get("provenance") is not None:
        data["provenance"] = DiscussionProvenance.from_dict(data["provenance"])
    return DraftOperation(**data)
