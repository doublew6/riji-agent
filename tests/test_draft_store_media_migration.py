import sqlite3
from datetime import date
from pathlib import Path

from riji_agent.drafts.models import Draft, DraftOperation, DraftStatus
from riji_agent.drafts.store import DraftStore


def test_old_text_only_database_is_migrated_without_losing_drafts(tmp_path: Path) -> None:
    database = tmp_path / "drafts.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE drafts (
            draft_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
            persona_id TEXT NOT NULL, target_date TEXT NOT NULL, operations TEXT NOT NULL,
            token TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, source_id TEXT, after_hash TEXT
        );
        INSERT INTO drafts VALUES (
            'old', 'u1', 's1', 'gentle', '2026-07-11',
            '[["Notes", "旧的纯文字草稿"]]', 'token', 'awaiting_confirmation',
            '2026-07-11T08:00:00+00:00', '2026-07-11T08:30:00+00:00', NULL, NULL
        );
        """
    )
    connection.commit()
    connection.close()

    store = DraftStore(database)
    draft = store.get("old")
    assert draft is not None
    assert draft.operations == (DraftOperation("Notes", "旧的纯文字草稿"),)
    assert draft.attachments == ()

    store.save(
        Draft(
            draft_id="new",
            user_id="u1",
            session_id="s1",
            persona_id="gentle",
            target_date=date(2026, 7, 11),
            operations=(DraftOperation("Notes", "新草稿"),),
            attachments=(),
            token="token-2",
            status=DraftStatus.AWAITING,
            created_at="2026-07-11T08:01:00+00:00",
            expires_at="2026-07-11T08:31:00+00:00",
        )
    )
    assert store.get("new").attachments == ()
