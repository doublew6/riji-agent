"""Local SQLite FTS5 index over the journal vault.

The index stores parsed metadata plus a full-text table. It supports a first
full build and incremental updates keyed on content hashes, so a single edited
note only re-indexes that note. The vault itself is never modified.
"""

from __future__ import annotations

import functools
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from riji_agent.journal.embedding import EmbeddingProvider, cosine
from riji_agent.journal.models import NoteKind, NoteSummary, ParsedNote
from riji_agent.journal.parser import (
    JournalParseError,
    SlowFileError,
    build_source_id,
    iter_note_files,
    parse_note,
    read_file_bytes,
)

# Progress callback signature: (done, total, action, source_id).
ProgressFn = Callable[[int, int, str, str], None]

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

_QUERY_TERM_RE = re.compile(r"[^\s\"'()]+")


@dataclass
class IndexStats:
    """Outcome of a build or incremental update."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    # Sanitized ids (wikilink targets, never absolute paths) of skipped notes.
    skipped_sources: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SearchHit:
    """A single full-text match, carrying the ``private`` flag downstream."""

    source_id: str
    title: str
    kind: NoteKind
    note_date: Optional[Date]
    private: bool
    snippet: str


def _rrf_fuse(keyword_ids: List[str], semantic_ids: List[str], limit: int, *, k: int = 60) -> List[str]:
    """Reciprocal-rank fusion of two ranked id lists."""
    scores: Dict[str, float] = {}
    for ranking in (keyword_ids, semantic_ids):
        for rank, source_id in enumerate(ranking):
            scores[source_id] = scores.get(source_id, 0.0) + 1.0 / (k + rank + 1)
    ordered = sorted(scores, key=lambda sid: (-scores[sid], sid))
    return ordered[:limit]


def _synchronized(method):
    """Serialize a public index method on the instance's reentrant lock.

    The background index scheduler and request-path reads share one SQLite
    connection; this lock makes their access safe without a connection per
    caller. Internal helpers stay unlocked (always called under a held lock).
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class JournalIndex:
    """Read-only-over-the-vault index backed by a local SQLite database."""

    def __init__(
        self,
        database_path: Path,
        journal_root: Path,
        *,
        embedder: Optional[EmbeddingProvider] = None,
        file_read_timeout: Optional[float] = None,
    ) -> None:
        self._journal_root = Path(journal_root)
        self._database_path = Path(database_path)
        self._embedder = embedder
        # Per-file read budget for the full walk; None disables it.
        self._file_read_timeout = file_read_timeout
        # Guards the shared connection across the scheduler and request threads.
        self._lock = threading.RLock()
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # check_same_thread=False because access is serialized by ``self._lock``.
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
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings (source_id TEXT PRIMARY KEY, vector TEXT NOT NULL)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------ build

    @_synchronized
    def build_index(
        self, *, rebuild: bool = False, progress: Optional[ProgressFn] = None
    ) -> IndexStats:
        """Walk the vault and (re)index changed notes; remove deleted ones.

        Resilient to a slow or unavailable file: one that cannot be read within
        the per-file budget (or that fails to parse) is skipped and counted, the
        run continues, and a skipped note keeps its existing index entry rather
        than being treated as deleted. ``progress`` is called per file as
        ``(done, total, action, source_id)`` for observable long runs.
        """
        if rebuild:
            self._conn.execute("DELETE FROM notes")
            self._conn.execute("DELETE FROM notes_fts")
            self._conn.execute("DELETE FROM embeddings")

        existing: Dict[str, str] = {
            row["source_id"]: row["content_hash"]
            for row in self._conn.execute("SELECT source_id, content_hash FROM notes")
        }
        stats = IndexStats()
        seen: Set[str] = set()

        timeout = self._file_read_timeout
        reader = (lambda p: read_file_bytes(p, timeout)) if timeout else None
        files = list(iter_note_files(self._journal_root))
        total = len(files)

        for done, path in enumerate(files, start=1):
            try:
                note = parse_note(path, self._journal_root, reader=reader)
            except (SlowFileError, JournalParseError, OSError):
                # Unreadable/slow/malformed: skip without deleting any prior
                # entry. Only the sanitized wikilink id is recorded.
                source_id = build_source_id(path, self._journal_root)
                seen.add(source_id)
                stats.skipped += 1
                stats.skipped_sources.append(source_id)
                if progress is not None:
                    progress(done, total, "skipped", source_id)
                continue

            seen.add(note.source_id)
            previous_hash = existing.get(note.source_id)
            if previous_hash == note.content_hash:
                stats.unchanged += 1
                action = "unchanged"
            else:
                self._upsert(note, path.stat().st_mtime)
                if previous_hash is None:
                    stats.added += 1
                    action = "added"
                else:
                    stats.updated += 1
                    action = "updated"
            if progress is not None:
                progress(done, total, action, note.source_id)

        for source_id in existing:
            if source_id not in seen:
                self._delete(source_id)
                stats.deleted += 1

        self._conn.commit()
        return stats

    @_synchronized
    def update_note(self, path: Path) -> ParsedNote:
        """Re-index a single note after it changed, without a full walk."""
        note = parse_note(path, self._journal_root)
        self._upsert(note, Path(path).stat().st_mtime)
        self._conn.commit()
        return note

    @_synchronized
    def remove_source(self, source_id: str) -> None:
        """Drop a note that was deleted from the vault."""
        self._delete(source_id)
        self._conn.commit()

    # ------------------------------------------------------------------ query

    @_synchronized
    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
        return int(row["n"])

    @_synchronized
    def last_indexed_at(self) -> Optional[str]:
        """Most recent note index timestamp (ISO 8601), or None if empty."""
        row = self._conn.execute("SELECT MAX(indexed_at) AS t FROM notes").fetchone()
        return row["t"] if row and row["t"] else None

    @_synchronized
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

    @_synchronized
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
        """Keyword search, or hybrid keyword+semantic when an embedder is set.

        Set ``include_private=False`` to drop private notes. Raises
        ``sqlite3.OperationalError`` for malformed FTS query syntax, which the
        caller is expected to translate into a safe error.
        """
        pool = max(limit, 20) if self._embedder is not None else limit
        fts_hits = self._fts_search(
            query, limit=pool, include_private=include_private,
            date_from=date_from, date_to=date_to, tags=tags,
        )
        keyword_hits = fts_hits or self._like_search(
            query, limit=pool, include_private=include_private,
            date_from=date_from, date_to=date_to, tags=tags,
        )
        if self._embedder is None:
            return keyword_hits[:limit]

        semantic_ids = self._semantic_search(
            query, limit=pool, include_private=include_private,
            date_from=date_from, date_to=date_to, tags=tags,
        )
        fused = _rrf_fuse([h.source_id for h in keyword_hits], semantic_ids, limit)
        keyword_by_id = {h.source_id: h for h in keyword_hits}
        return [keyword_by_id[sid] if sid in keyword_by_id else self._semantic_hit(sid) for sid in fused]

    def _fts_search(
        self, query, *, limit, include_private, date_from, date_to, tags
    ) -> List[SearchHit]:
        clauses = ["notes_fts MATCH ?"]
        params: List[object] = [query]
        clauses += self._filter_clauses(include_private, date_from, date_to, tags, params, prefix="n.")
        params.append(limit)
        sql = (
            "SELECT n.source_id, n.title, n.kind, n.note_date, n.private, "
            "snippet(notes_fts, 3, '[', ']', '…', 12) AS snippet "
            "FROM notes_fts JOIN notes n ON n.source_id = notes_fts.source_id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY rank LIMIT ?"
        )
        return [self._row_to_hit(row, row["snippet"]) for row in self._conn.execute(sql, params)]

    def _like_search(
        self, query, *, limit, include_private, date_from, date_to, tags
    ) -> List[SearchHit]:
        """Fallback for CJK/short-token queries that FTS tokenizers miss.

        SQLite FTS5 trigram does not match one- or two-character CJK queries,
        and older unicode61 indexes do not segment Chinese prose well. The
        fallback stays local, keeps the same privacy/date/tag filters, returns
        capped snippets, and is used only when the FTS search returned no rows.
        """
        terms = _like_terms(query)
        if not terms:
            return []

        params: List[object] = []
        clauses = self._filter_clauses(include_private, date_from, date_to, tags, params, prefix="n.")
        like_clauses: List[str] = []
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            like_clauses.append(
                "(notes_fts.body LIKE ? ESCAPE '\\' OR notes_fts.title LIKE ? ESCAPE '\\' "
                "OR notes_fts.tags LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern])
        clauses.append("(" + " OR ".join(like_clauses) + ")")
        params.append(limit)
        sql = (
            "SELECT n.source_id, n.title, n.kind, n.note_date, n.private, notes_fts.body AS body "
            "FROM notes_fts JOIN notes n ON n.source_id = notes_fts.source_id "
            "WHERE " + " AND ".join(clauses)
            + " ORDER BY n.note_date DESC, n.source_id LIMIT ?"
        )
        return [
            self._row_to_hit(row, _like_snippet(row["body"] or "", terms))
            for row in self._conn.execute(sql, params)
        ]

    def _semantic_search(
        self, query, *, limit, include_private, date_from, date_to, tags
    ) -> List[str]:
        query_vec = self._embedder.embed([query])[0]
        clauses = self._filter_clauses(include_private, date_from, date_to, tags, params := [], prefix="n.")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT n.source_id AS source_id, e.vector AS vector FROM embeddings e "
            "JOIN notes n ON n.source_id = e.source_id" + where
        )
        scored: List[Tuple[float, str]] = []
        for row in self._conn.execute(sql, params):
            scored.append((cosine(query_vec, json.loads(row["vector"])), row["source_id"]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [sid for score, sid in scored[:limit] if score > 0.0]

    @staticmethod
    def _filter_clauses(include_private, date_from, date_to, tags, params, *, prefix) -> List[str]:
        clauses: List[str] = []
        if not include_private:
            clauses.append(f"{prefix}private = 0")
        if date_from is not None:
            clauses.append(f"{prefix}note_date >= ?")
            params.append(date_from.isoformat())
        if date_to is not None:
            clauses.append(f"{prefix}note_date <= ?")
            params.append(date_to.isoformat())
        for tag in tags or ():
            clauses.append(f"{prefix}tags LIKE ?")
            params.append(f'%"{tag}"%')
        return clauses

    def _semantic_hit(self, source_id: str) -> SearchHit:
        row = self._conn.execute("SELECT * FROM notes WHERE source_id = ?", (source_id,)).fetchone()
        body_row = self._conn.execute(
            "SELECT body FROM notes_fts WHERE source_id = ?", (source_id,)
        ).fetchone()
        snippet = (body_row["body"][:80] if body_row else "")
        return self._row_to_hit(row, snippet)

    @staticmethod
    def _row_to_hit(row: sqlite3.Row, snippet: str) -> SearchHit:
        return SearchHit(
            source_id=row["source_id"],
            title=row["title"],
            kind=NoteKind(row["kind"]),
            note_date=Date.fromisoformat(row["note_date"]) if row["note_date"] else None,
            private=bool(row["private"]),
            snippet=snippet,
        )

    @_synchronized
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
        if self._embedder is not None:
            vector = self._embedder.embed([f"{note.title}\n{note.body}"])[0]
            self._conn.execute(
                "INSERT OR REPLACE INTO embeddings (source_id, vector) VALUES (?, ?)",
                (note.source_id, json.dumps(vector)),
            )

    def _delete(self, source_id: str) -> None:
        self._conn.execute("DELETE FROM notes WHERE source_id = ?", (source_id,))
        self._conn.execute("DELETE FROM notes_fts WHERE source_id = ?", (source_id,))
        self._conn.execute("DELETE FROM embeddings WHERE source_id = ?", (source_id,))

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


def _like_terms(query: str) -> List[str]:
    terms: List[str] = []
    for match in _QUERY_TERM_RE.finditer(query):
        term = match.group(0).strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_snippet(body: str, terms: Sequence[str], *, radius: int = 80) -> str:
    if not body:
        return ""
    lower = body.lower()
    positions = [lower.find(term.lower()) for term in terms if term]
    positions = [pos for pos in positions if pos >= 0]
    if not positions:
        return body[: radius * 2]
    pos = min(positions)
    start = max(0, pos - radius)
    end = min(len(body), pos + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return prefix + body[start:end] + suffix
