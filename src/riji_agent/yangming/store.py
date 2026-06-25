"""SQLite knowledge base for Wang Yangming thought, separate from the journal."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List

from riji_agent.yangming.models import (
    CitationHit,
    CitationKind,
    YangmingChunk,
    YangmingDocument,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id  TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    source  TEXT NOT NULL,
    version TEXT NOT NULL,
    note    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id   TEXT NOT NULL,
    ref      TEXT NOT NULL,
    kind     TEXT NOT NULL,
    text     TEXT NOT NULL
);
"""

_FTS_TRIGRAM = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
    "USING fts5(chunk_id UNINDEXED, text, tokenize='trigram')"
)
_FTS_UNICODE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
    "USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')"
)


class YangmingKB:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.execute(_FTS_TRIGRAM)
        except sqlite3.OperationalError:
            self._conn.execute(_FTS_UNICODE)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_document(self, document: YangmingDocument) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO documents (doc_id, title, source, version, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (document.doc_id, document.title, document.source, document.version, document.note),
        )
        self._conn.commit()

    def add_chunk(self, chunk: YangmingChunk) -> None:
        self._conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk.chunk_id,))
        self._conn.execute(
            "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)", (chunk.chunk_id, chunk.text)
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO chunks (chunk_id, doc_id, ref, kind, text) VALUES (?, ?, ?, ?, ?)",
            (chunk.chunk_id, chunk.doc_id, chunk.ref, chunk.kind.value, chunk.text),
        )
        self._conn.commit()

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"])

    def search(self, query: str, *, limit: int = 5) -> List[CitationHit]:
        sql = (
            "SELECT c.chunk_id, c.ref, c.kind, c.text, d.title, d.source, d.version "
            "FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id "
            "JOIN documents d ON d.doc_id = c.doc_id "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?"
        )
        hits: List[CitationHit] = []
        for row in self._conn.execute(sql, (query, limit)):
            hits.append(
                CitationHit(
                    chunk_id=row["chunk_id"],
                    ref=row["ref"],
                    kind=CitationKind(row["kind"]),
                    text=row["text"],
                    title=row["title"],
                    source=row["source"],
                    version=row["version"],
                )
            )
        return hits
