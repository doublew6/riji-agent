"""SQLite store for self-evolution proposals."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from riji_agent.evolution.models import EvolutionProposal, EvolutionProposalStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_proposals (
    proposal_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class EvolutionProposalStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save(self, proposal: EvolutionProposal) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO evolution_proposals "
            "(proposal_id, user_id, session_id, category, title, body, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal.proposal_id,
                proposal.user_id,
                proposal.session_id,
                proposal.category,
                proposal.title,
                proposal.body,
                proposal.status.value,
                proposal.created_at,
                proposal.updated_at,
            ),
        )
        self._conn.commit()

    def get(self, proposal_id: str) -> Optional[EvolutionProposal]:
        row = self._conn.execute(
            "SELECT * FROM evolution_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return _row_to_proposal(row) if row else None

    def latest_awaiting_for_session(self, session_id: str) -> Optional[EvolutionProposal]:
        row = self._conn.execute(
            "SELECT * FROM evolution_proposals WHERE session_id = ? AND status = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id, EvolutionProposalStatus.AWAITING.value),
        ).fetchone()
        return _row_to_proposal(row) if row else None


def _row_to_proposal(row: sqlite3.Row) -> EvolutionProposal:
    return EvolutionProposal(
        proposal_id=row["proposal_id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        category=row["category"],
        title=row["title"],
        body=row["body"],
        status=EvolutionProposalStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
