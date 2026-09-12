"""One local transaction owner for identities, runs, artifacts and deliveries."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TypeVar

from riji_agent.mentors.models import Record

T = TypeVar("T", bound=Record)

SCHEMA = """
CREATE TABLE IF NOT EXISTS mentor_records (
    kind TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL, PRIMARY KEY(kind,id)
);
CREATE INDEX IF NOT EXISTS mentor_record_owner ON mentor_records(kind,owner);
CREATE TABLE IF NOT EXISTS mentor_keys (
    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,key)
);
CREATE TABLE IF NOT EXISTS mentor_commands (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mentor_steps (
    key TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
    artifact_id TEXT, attempted_at REAL NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mentor_budgets (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
    request_limit INTEGER NOT NULL, elapsed REAL NOT NULL DEFAULT 0,
    time_limit REAL NOT NULL, active_since REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mentor_source_usage (
    owner TEXT NOT NULL, source_id TEXT NOT NULL, version TEXT NOT NULL,
    chars INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,source_id,version)
);
CREATE TABLE IF NOT EXISTS mentor_deleted (conversation_id TEXT PRIMARY KEY, deleted_at REAL NOT NULL);
"""


def key(*parts: str) -> str:
    return json.dumps(parts, separators=(",", ":"))


class MentorStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("mentor_store_symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.transaction() as db:
            db.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def get(db: sqlite3.Connection, kind: str, identifier: str, model: type[T]) -> T | None:
        row = db.execute("SELECT value FROM mentor_records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    @staticmethod
    def put(db: sqlite3.Connection, kind: str, record: Record, owner: str = "", identifier: str | None = None) -> None:
        db.execute(
            "INSERT INTO mentor_records VALUES (?, ?, ?, ?) "
            "ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value, owner=excluded.owner",
            (kind, identifier or record.id, owner, record.model_dump_json()),
        )

    @staticmethod
    def records(db: sqlite3.Connection, kind: str, owner: str, model: type[T]) -> tuple[T, ...]:
        rows = db.execute("SELECT value FROM mentor_records WHERE kind=? AND owner=? ORDER BY rowid", (kind, owner))
        return tuple(model.model_validate_json(row[0]) for row in rows)

    @staticmethod
    def lookup(db: sqlite3.Connection, kind: str, identity: str) -> str | None:
        row = db.execute("SELECT value FROM mentor_keys WHERE kind=? AND key=?", (kind, identity)).fetchone()
        return row[0] if row else None

    @staticmethod
    def bind(db: sqlite3.Connection, kind: str, identity: str, value: str) -> None:
        db.execute("INSERT INTO mentor_keys VALUES (?,?,?) ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value", (kind, identity, value))

    def read(self, kind: str, identifier: str, model: type[T]) -> T | None:
        with self.transaction() as db:
            return self.get(db, kind, identifier, model)

    def list(self, kind: str, owner: str, model: type[T]) -> tuple[T, ...]:
        with self.transaction() as db:
            return self.records(db, kind, owner, model)
