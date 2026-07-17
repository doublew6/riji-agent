"""SQLite persistence for drafts and their confirmation tokens."""

from __future__ import annotations

import json
import sqlite3
from datetime import date as Date
from pathlib import Path
from typing import List, Optional

from riji_agent.drafts.models import Draft, DraftOperation, DraftStatus
from riji_agent.media.models import MediaAttachment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    draft_id   TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    target_date TEXT NOT NULL,
    operations TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    token      TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_id  TEXT,
    after_hash TEXT
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
        self._ensure_attachments_column()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save(self, draft: Draft) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO drafts (draft_id, user_id, session_id, persona_id, "
            "target_date, operations, attachments, token, status, created_at, expires_at, "
            "source_id, after_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft.draft_id,
                draft.user_id,
                draft.session_id,
                draft.persona_id,
                draft.target_date.isoformat(),
                json.dumps([[o.section, o.content] for o in draft.operations], ensure_ascii=False),
                json.dumps([_attachment_to_dict(item) for item in draft.attachments]),
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

    def claim_for_commit(self, draft_id: str) -> bool:
        """Atomically move a draft from AWAITING to COMMITTING.

        Returns ``True`` only for the caller that won the claim. This single
        conditional UPDATE is the cross-process mutex for the commit: SQLite
        serializes writers, so among several concurrent confirmations only one
        matches the still-awaiting row; the others affect zero rows and lose,
        which closes the check-then-act race without relying on a process lock.
        """
        cursor = self._conn.execute(
            "UPDATE drafts SET status = ? WHERE draft_id = ? AND status = ?",
            (DraftStatus.COMMITTING.value, draft_id, DraftStatus.AWAITING.value),
        )
        self._conn.commit()
        return cursor.rowcount == 1

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

    @staticmethod
    def _to_draft(row: sqlite3.Row) -> Draft:
        operations = tuple(
            DraftOperation(section=item[0], content=item[1])
            for item in json.loads(row["operations"])
        )
        attachments = tuple(
            MediaAttachment(**item) for item in json.loads(row["attachments"] or "[]")
        )
        return Draft(
            draft_id=row["draft_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            persona_id=row["persona_id"],
            target_date=Date.fromisoformat(row["target_date"]),
            operations=operations,
            attachments=attachments,
            token=row["token"],
            status=DraftStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            source_id=row["source_id"],
            after_hash=row["after_hash"],
        )

    def _ensure_attachments_column(self) -> None:
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(drafts)").fetchall()
        }
        if "attachments" not in columns:
            self._conn.execute(
                "ALTER TABLE drafts ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'"
            )


def _attachment_to_dict(item: MediaAttachment) -> dict:
    return {
        "attachment_id": item.attachment_id,
        "event_id": item.event_id,
        "part_index": item.part_index,
        "sha256": item.sha256,
        "media_type": item.media_type,
        "extension": item.extension,
        "size_bytes": item.size_bytes,
        "staged_path": item.staged_path,
    }
