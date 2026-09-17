"""Private pairing, revocation and conservative durable operation receipts."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


class MobileError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fingerprint(value: dict) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class MobileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise MobileError("mobile_storage_unavailable", 503)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, principal TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pairings (code_hash TEXT PRIMARY KEY, principal TEXT NOT NULL,
                    expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS bindings (device TEXT NOT NULL, persona TEXT NOT NULL,
                    chat TEXT UNIQUE NOT NULL, PRIMARY KEY(device,persona));
                CREATE TABLE IF NOT EXISTS operations (device TEXT NOT NULL, id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, error TEXT, PRIMARY KEY(device,id));
            """)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def pairing(self, principal: str, *, lifetime: int = 600) -> str:
        code = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute("INSERT INTO pairings VALUES (?,?,?,0)", (digest(code), principal, time.time() + lifetime))
        return code

    def pair(self, code: str, name: str) -> dict:
        token, identifier = secrets.token_urlsafe(48), str(uuid4())
        with self.transaction() as db:
            row = db.execute("SELECT * FROM pairings WHERE code_hash=?", (digest(code),)).fetchone()
            if row is None or row["used"] or row["expires"] <= time.time():
                raise MobileError("pairing_invalid_or_expired", 401)
            db.execute("UPDATE pairings SET used=1 WHERE code_hash=?", (digest(code),))
            db.execute("INSERT INTO devices VALUES (?,?,?,?,?,0)",
                       (identifier, row["principal"], digest(token), name, time.time()))
        return {"api_version": 1, "device_id": identifier, "device_token": token}

    def authenticate(self, token: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (digest(token),)).fetchone()
        if not token or row is None:
            raise MobileError("device_authentication_required", 401)
        return dict(row)

    def provision(self, principal: str, name: str) -> dict:
        """Issue a distinct device only after the local operator verifies its owner."""
        token, identifier = secrets.token_urlsafe(48), str(uuid4())
        with self.transaction() as db:
            db.execute("INSERT INTO devices VALUES (?,?,?,?,?,0)",
                       (identifier, principal, digest(token), name, time.time()))
        return {"device_id": identifier, "device_token": token}

    def device(self, identifier: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM devices WHERE id=? AND revoked=0", (identifier,)).fetchone()
        if row is None:
            raise MobileError("device_authentication_required", 401)
        return dict(row)

    def revoke(self, identifier: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (identifier,))

    def devices(self) -> list[dict]:
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT id,name,created,revoked FROM devices ORDER BY created")]

    def binding(self, device: str, persona: str) -> str | None:
        with self.transaction() as db:
            row = db.execute("SELECT chat FROM bindings WHERE device=? AND persona=?", (device, persona)).fetchone()
        return row[0] if row else None

    def bind(self, device: str, persona: str, chat: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO bindings VALUES (?,?,?)", (device, persona, chat))

    def binding_device(self, chat: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT d.* FROM devices d JOIN bindings b ON b.device=d.id "
                             "WHERE b.chat=? AND d.revoked=0", (chat,)).fetchone()
        if row is None:
            raise MobileError("device_authentication_required", 401)
        return dict(row)

    def begin(self, device: str, operation: str, kind: str, body: dict) -> tuple[bool, dict]:
        value = fingerprint({"kind": kind, "body": body})
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE device=? AND id=?", (device, operation)).fetchone()
            if row:
                if row["fingerprint"] != value:
                    raise MobileError("operation_conflict")
                return False, self._receipt(row)
            db.execute("INSERT INTO operations VALUES (?,?,?,?, 'pending',NULL,NULL)", (device, operation, value, kind))
        return True, self.receipt(device, operation)

    def finish(self, device: str, operation: str, state: str, *, result: dict | None = None,
               error: str | None = None) -> dict:
        with self.transaction() as db:
            db.execute("UPDATE operations SET state=?,result=?,error=? WHERE device=? AND id=?",
                       (state, json.dumps(result) if result is not None else None, error, device, operation))
        return self.receipt(device, operation)

    def receipt(self, device: str, operation: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE device=? AND id=?", (device, operation)).fetchone()
        if row is None:
            raise MobileError("operation_not_found", 404)
        return self._receipt(row)

    def recover(self) -> None:
        with self.transaction() as db:
            db.execute("UPDATE operations SET state='unknown',error='operation_interrupted' WHERE state='pending'")

    @staticmethod
    def _receipt(row: sqlite3.Row) -> dict:
        return {"operation_id": row["id"], "kind": row["kind"], "state": row["state"],
                "result": json.loads(row["result"]) if row["result"] else None, "error": row["error"]}
