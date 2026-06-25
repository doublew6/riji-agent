"""Local SQLite FTS5 index over the journal vault.

The index stores parsed metadata plus a full-text table. It supports a first
full build and incremental updates keyed on content hashes, so a single edited
note only re-indexes that note. The vault itself is never modified.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from riji_agent.journal.models import NoteKind, NoteSummary, ParsedNote
from riji_agent.journal.parser import iter_note_files, parse_note

_NOTES_TABLE = """
CREATE TABLE IF NOT EXISTS notes (
    source_id     TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL,
    kind          TEXT NOT NULL,
    note_date     TEXT,
    title         TEXT NOT NULL,
    tags          TEXT NOT NULL,
    private       INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    mtime         REAL NOT NULL,
    indexed_at    TEXT NOT NULL
);
"""

# Trigram tokenisation gives usable substring search for CJK journals; older
# SQLite builds without it fall back to the default unicode tokeniser.
_FTS_TRIGRAM = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
    "USING fts5(source_id UNINDEXED, title, tags, body, tokenize='trigram')"
)
_FTS_UNICODE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
    "USING fts5(source_id UNINDEXED, title, tags, body, tokenize='unicode61')"
)


@dataclass
class IndexStats:
    """Outcome of a build or incremental update."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0


@dataclass(frozen=True)
class SearchHit:
    """A single full-text match, carrying the ``private`` flag downstream."""

    source_id: str
    title: str
    kind: NoteKind
    note_date: Optional[Date]
    private: bool
    snippet: str


class JournalIndex:
    """Read-only-over-the-vault index backed by a local SQLite database."""

    def __init__(self, database_path: Path, journal_root: Path) -> None:
        self._journal_root = Path(journal_root)
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Access is serialized by the gateway lock when wired into the request
        # path; allow use across FastAPI threadpool threads.
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def __enter__(self) -> "JournalIndex":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.execute(_NOTES_TABLE)
        try:
            self._conn.execute(_FTS_TRIGRAM)
        except sqlite3.OperationalError:
            self._conn.execute(_FTS_UNICODE)
        self._conn.commit()

    # ------------------------------------------------------------------ build

    def build_index(self, *, rebuild: bool = False) -> IndexStats:
        """Walk the vault and (re)index changed notes; remove deleted ones."""
        if rebuild:
            self._conn.execute("DELETE FROM notes")
            self._conn.execute("DELETE FROM notes_fts")

        existing: Dict[str, str] = {
            row["source_id"]: row["content_hash"]
            for row in self._conn.execute("SELECT source_id, content_hash FROM notes")
        }
        stats = IndexStats()
        seen: Set[str] = set()

        for path in iter_note_files(self._journal_root):
            note = parse_note(path, self._journal_root)
            seen.add(note.source_id)
            previous_hash = existing.get(note.source_id)
            if previous_hash == note.content_hash:
                stats.unchanged += 1
                continue
            self._upsert(note, path.stat().st_mtime)
            if previous_hash is None:
                stats.added += 1
            else:
                stats.updated += 1

        for source_id in existing:
            if source_id not in seen:
                self._delete(source_id)
                stats.deleted += 1

        self._conn.commit()
        return stats

    def update_note(self, path: Path) -> ParsedNote:
        """Re-index a single note after it changed, without a full walk."""
        note = parse_note(path, self._journal_root)
        self._upsert(note, Path(path).stat().st_mtime)
        self._conn.commit()
        return note

    def remove_source(self, source_id: str) -> None:
        """Drop a note that was deleted from the vault."""
        self._delete(source_id)
        self._conn.commit()

    # ------------------------------------------------------------------ query

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
        return int(row["n"])

    def get(self, source_id: str) -> Optional[ParsedNote]:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return None
        body_row = self._conn.execute(
            "SELECT body FROM notes_fts WHERE source_id = ?", (source_id,)
        ).fetchone()
        return self._row_to_note(row, body_row["body"] if body_row else "")

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        include_private: bool = True,
        date_from: Optional[Date] = None,
        date_to: Optional[Date] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        """Full-text search with optional date-range and tag filters.

        Set ``include_private=False`` to drop private notes. Raises
        ``sqlite3.OperationalError`` for malformed FTS query syntax, which the
        caller is expected to translate into a safe error.
        """
        clauses = ["notes_fts MATCH ?"]
        params: List[object] = [query]
        if not include_private:
            clauses.append("n.private = 0")
        if date_from is not None:
            clauses.append("n.note_date >= ?")
            params.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("n.note_date <= ?")
            params.append(date_to.isoformat())
        for tag in tags or ():
            clauses.append("n.tags LIKE ?")
            params.append(f'%"{tag}"%')
        params.append(limit)

        sql = (
            "SELECT n.source_id, n.title, n.kind, n.note_date, n.private, "
            "snippet(notes_fts, 3, '[', ']', '…', 12) AS snippet "
            "FROM notes_fts JOIN notes n ON n.source_id = notes_fts.source_id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY rank LIMIT ?"
        )
        hits: List[SearchHit] = []
        for row in self._conn.execute(sql, params):
            hits.append(
                SearchHit(
                    source_id=row["source_id"],
                    title=row["title"],
                    kind=NoteKind(row["kind"]),
                    note_date=Date.fromisoformat(row["note_date"]) if row["note_date"] else None,
                    private=bool(row["private"]),
                    snippet=row["snippet"],
                )
            )
        return hits

    def list_notes(
        self,
        *,
        kind: Optional[NoteKind] = None,
        date_from: Optional[Date] = None,
        date_to: Optional[Date] = None,
        include_private: bool = True,
        limit: int = 50,
    ) -> List[NoteSummary]:
        """List note metadata (no body), newest first, with optional filters."""
        clauses: List[str] = []
        params: List[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if not include_private:
            clauses.append("private = 0")
        if date_from is not None:
            clauses.append("note_date >= ?")
            params.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("note_date <= ?")
            params.append(date_to.isoformat())
        params.append(limit)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT source_id, kind, note_date, title, private FROM notes"
            + where
            + " ORDER BY note_date DESC, source_id LIMIT ?"
        )
        summaries: List[NoteSummary] = []
        for row in self._conn.execute(sql, params):
            summaries.append(
                NoteSummary(
                    source_id=row["source_id"],
                    kind=NoteKind(row["kind"]),
                    note_date=Date.fromisoformat(row["note_date"]) if row["note_date"] else None,
                    title=row["title"],
                    private=bool(row["private"]),
                )
            )
        return summaries

    # --------------------------------------------------------------- internals

    def _upsert(self, note: ParsedNote, mtime: float) -> None:
        self._conn.execute("DELETE FROM notes_fts WHERE source_id = ?", (note.source_id,))
        self._conn.execute(
            "INSERT INTO notes_fts (source_id, title, tags, body) VALUES (?, ?, ?, ?)",
            (note.source_id, note.title, " ".join(note.tags), note.body),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO notes "
            "(source_id, relative_path, kind, note_date, title, tags, private, "
            "content_hash, mtime, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note.source_id,
                note.relative_path,
                note.kind.value,
                note.note_date.isoformat() if note.note_date else None,
                note.title,
                json.dumps(list(note.tags)),
                int(note.private),
                note.content_hash,
                mtime,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def _delete(self, source_id: str) -> None:
        self._conn.execute("DELETE FROM notes WHERE source_id = ?", (source_id,))
        self._conn.execute("DELETE FROM notes_fts WHERE source_id = ?", (source_id,))

    @staticmethod
    def _row_to_note(row: sqlite3.Row, body: str) -> ParsedNote:
        return ParsedNote(
            source_id=row["source_id"],
            relative_path=row["relative_path"],
            kind=NoteKind(row["kind"]),
            note_date=Date.fromisoformat(row["note_date"]) if row["note_date"] else None,
            title=row["title"],
            tags=tuple(json.loads(row["tags"])),
            body=body,
            private=bool(row["private"]),
            content_hash=row["content_hash"],
        )
