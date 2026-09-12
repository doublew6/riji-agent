"""Bounded transport receipts, separate from business history and the vault."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from riji_agent.mentors.models import Envelope, MentorError
from riji_agent.mentors.store import key


class ReceiverSpool:
    def __init__(self, path: Path, application_id: str,
                 clock: Callable[[], float] = time.time, capacity: int = 200) -> None:
        self.path, self.application_id, self.clock, self.capacity = path, application_id, clock, capacity
        self._lock = threading.RLock()
        self._volatile: dict[str, Envelope] = {}
        if path.is_symlink() or path.parent.is_symlink():
            raise MentorError("receiver_spool_path_invalid")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.stat().st_mode & 0o077:
            raise MentorError("receiver_spool_permissions_invalid")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        if path.stat().st_mode & 0o077:
            raise MentorError("receiver_spool_permissions_invalid")
        with self.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, created REAL NOT NULL, message_id TEXT NOT NULL DEFAULT '')""")
            # Only queued ordinary messages are safe to resume automatically.
            db.execute("UPDATE receipts SET state='unknown', payload='' WHERE state IN ('processing','sending')")
            db.execute("UPDATE receipts SET state='expired', payload='' WHERE state='queued' AND payload=''")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=1)
            try:
                with db:
                    db.row_factory = sqlite3.Row
                    db.execute("PRAGMA secure_delete=ON")
                    db.execute("BEGIN IMMEDIATE")
                    yield db
            finally:
                db.close()

    def enqueue(self, message: Envelope) -> str:
        identifier = hashlib.sha256(key(self.application_id, message.external_chat_id,
            message.message_id, message.action_id).encode()).hexdigest()
        fingerprint = hashlib.sha256(message.model_dump_json(exclude={"delivery_id"}).encode()).hexdigest()
        pairing = message.text.strip().startswith("/绑定")
        with self.transaction() as db:
            self._expire(db)
            saved = db.execute("SELECT fingerprint FROM receipts WHERE id=?", (identifier,)).fetchone()
            if saved:
                if saved["fingerprint"] != fingerprint:
                    raise MentorError("receiver_event_conflict")
                return identifier
            count = db.execute("SELECT count(*) FROM receipts WHERE state IN ('queued','processing','sending')").fetchone()[0]
            if count >= self.capacity:
                raise MentorError("receiver_spool_full")
            db.execute("INSERT INTO receipts (id,fingerprint,state,payload,created) VALUES (?,?,'queued',?,?)",
                       (identifier, fingerprint, "" if pairing else message.model_dump_json(), self.clock()))
            if pairing:
                self._volatile[identifier] = message
        return identifier

    def _expire(self, db: sqlite3.Connection) -> None:
        expired = db.execute("SELECT id FROM receipts WHERE state='queued' AND (created<? OR (payload='' AND created<?))",
                             (self.clock() - 86400, self.clock() - 600)).fetchall()
        for row in expired:
            self._volatile.pop(row["id"], None)
            db.execute("UPDATE receipts SET state='expired',payload='' WHERE id=?", (row["id"],))
        db.execute("DELETE FROM receipts WHERE state NOT IN ('queued','processing','sending') AND created<?",
                   (self.clock() - 7 * 86400,))
        # Bound terminal metadata even under high-volume rejected traffic.
        db.execute("""DELETE FROM receipts WHERE id IN (SELECT id FROM receipts
            WHERE state NOT IN ('queued','processing','sending') ORDER BY created DESC LIMIT -1 OFFSET 2000)""")

    def take(self) -> tuple[str, Envelope] | None:
        with self.transaction() as db:
            self._expire(db)
            row = db.execute("SELECT * FROM receipts WHERE state='queued' ORDER BY created,rowid LIMIT 1").fetchone()
            if row is None:
                return None
            message = Envelope.model_validate_json(row["payload"]) if row["payload"] else self._volatile.pop(row["id"], None)
            if message is None:
                db.execute("UPDATE receipts SET state='expired',payload='' WHERE id=?", (row["id"],))
                return None
            # Persist uncertainty before the first business side effect.
            db.execute("UPDATE receipts SET state='processing',payload='' WHERE id=?", (row["id"],))
            return row["id"], message

    def mark(self, identifier: str, state: str, message_id: str = "") -> None:
        if state not in {"sending", "done", "unknown", "rejected"}:
            raise MentorError("receiver_state_invalid")
        with self.transaction() as db:
            db.execute("UPDATE receipts SET state=?,message_id=?,payload='' WHERE id=?", (state, message_id, identifier))

    def status(self) -> dict[str, int]:
        with self.transaction() as db:
            self._expire(db)
            return dict(db.execute("SELECT state,count(*) FROM receipts GROUP BY state").fetchall())


def read_spool_status(path: Path) -> dict[str, int]:
    """Inspect a live receiver without applying startup recovery or reading text."""
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise MentorError("receiver_spool_permissions_invalid")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        return dict(db.execute("SELECT state,count(*) FROM receipts GROUP BY state").fetchall())
    finally:
        db.close()
