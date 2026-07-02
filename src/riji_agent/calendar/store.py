"""SQLite store for calendar drafts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from riji_agent.calendar.models import CalendarDraft, CalendarDraftStatus, CalendarEventDraft

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_drafts (
    draft_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    token TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    provider_event_id TEXT,
    journal_source_id TEXT
);
"""


class CalendarDraftStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save(self, draft: CalendarDraft) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO calendar_drafts "
            "(draft_id, user_id, session_id, persona_id, event_json, token, status, "
            "created_at, expires_at, provider_event_id, journal_source_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft.draft_id,
                draft.user_id,
                draft.session_id,
                draft.persona_id,
                _event_to_json(draft.event),
                draft.token,
                draft.status.value,
                draft.created_at,
                draft.expires_at,
                draft.provider_event_id,
                draft.journal_source_id,
            ),
        )
        self._conn.commit()

    def get(self, draft_id: str) -> Optional[CalendarDraft]:
        row = self._conn.execute(
            "SELECT * FROM calendar_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return _row_to_draft(row) if row else None

    def latest_awaiting_for_session(self, session_id: str) -> Optional[CalendarDraft]:
        row = self._conn.execute(
            "SELECT * FROM calendar_drafts WHERE session_id = ? AND status = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id, CalendarDraftStatus.AWAITING.value),
        ).fetchone()
        return _row_to_draft(row) if row else None

    def claim_for_create(self, draft_id: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE calendar_drafts SET status = ? WHERE draft_id = ? AND status = ?",
            (CalendarDraftStatus.CREATING.value, draft_id, CalendarDraftStatus.AWAITING.value),
        )
        self._conn.commit()
        return cursor.rowcount == 1


def _event_to_json(event: CalendarEventDraft) -> str:
    return json.dumps(
        {
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "timezone": event.timezone,
            "reminder_minutes": event.reminder_minutes,
            "description": event.description,
        },
        ensure_ascii=False,
    )


def _event_from_json(value: str) -> CalendarEventDraft:
    data = json.loads(value)
    return CalendarEventDraft(
        title=data["title"],
        start_at=datetime.fromisoformat(data["start_at"]),
        end_at=datetime.fromisoformat(data["end_at"]),
        timezone=data["timezone"],
        reminder_minutes=data.get("reminder_minutes"),
        description=data.get("description", ""),
    )


def _row_to_draft(row: sqlite3.Row) -> CalendarDraft:
    return CalendarDraft(
        draft_id=row["draft_id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        persona_id=row["persona_id"],
        event=_event_from_json(row["event_json"]),
        token=row["token"],
        status=CalendarDraftStatus(row["status"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        provider_event_id=row["provider_event_id"],
        journal_source_id=row["journal_source_id"],
    )
