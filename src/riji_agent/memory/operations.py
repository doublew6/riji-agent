"""SQLite queue and append-only audit log for long-term memory operations."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Optional, Sequence

from riji_agent.memory.models import (
    CaptureJob,
    CaptureJobStatus,
    MemoryChange,
    MemoryScope,
    NewMemoryChange,
)
from riji_agent.memory.organization_store import OrganizationStore

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS memory_capture_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_request_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    content TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    persona_id TEXT,
    scope TEXT NOT NULL,
    action TEXT NOT NULL,
    before_content TEXT,
    after_content TEXT,
    source_request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_status
    ON memory_capture_jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_memory_changes_user
    ON memory_change_log(user_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_capture_decisions
    ON memory_change_log(user_id, memory_id, source_request_id, action)
    WHERE action IN ('DEDUP_SKIP', 'NO_DURABLE_FACT');
CREATE TABLE IF NOT EXISTS memory_snapshot_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    status TEXT NOT NULL,
    error_code TEXT,
    generated_at TEXT,
    memory_count INTEGER,
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO memory_snapshot_state
    (singleton, status, updated_at) VALUES (1, 'pending', CURRENT_TIMESTAMP);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MemoryOperationsStore:
    def __init__(self, database_path: Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=5.0)
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._upgrade_capture_schema()
        self._conn.commit()
        self._path.chmod(0o600)
        self.organization = OrganizationStore(self._path)

    def _upgrade_capture_schema(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(memory_capture_jobs)")}
        for name, kind in (
            ("source_message_id", "INTEGER"),
            ("source_created_at", "TEXT"),
            ("extracted_json", "TEXT"),
        ):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE memory_capture_jobs ADD COLUMN {name} {kind}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_jobs_source_message "
            "ON memory_capture_jobs(source_message_id) WHERE source_message_id IS NOT NULL"
        )

    @_synchronized
    def close(self) -> None:
        self.organization.close()
        self._conn.close()

    @_synchronized
    def erase_memory_text(self, memory_id: str, user_id: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE memory_capture_jobs SET content=NULL,extracted_json=NULL,status='succeeded' "
                               "WHERE user_id=? AND source_request_id IN (SELECT source_request_id FROM "
                               "memory_change_log WHERE memory_id=? AND user_id=?)", (user_id, memory_id, user_id))
            self._conn.execute("UPDATE memory_change_log SET before_content=NULL,after_content=NULL "
                               "WHERE memory_id=? AND user_id=?", (memory_id, user_id))
        self.organization.clear_user(user_id)

    @_synchronized
    def enqueue(
        self,
        *,
        source_request_id: str,
        user_id: str,
        persona_id: str,
        session_id: str,
        content: str,
        source_message_id: Optional[int] = None,
        source_created_at: Optional[str] = None,
    ) -> int:
        now = _now()
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO memory_capture_jobs "
            "(source_request_id, user_id, persona_id, session_id, content, status, "
            "next_attempt_at, created_at, updated_at, source_message_id, source_created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_request_id,
                user_id,
                persona_id,
                session_id,
                content,
                CaptureJobStatus.PENDING.value,
                now,
                now,
                now,
                source_message_id,
                source_created_at,
            ),
        )
        self._conn.commit()
        if cursor.rowcount:
            return int(cursor.lastrowid)
        row = self._conn.execute(
            "SELECT id FROM memory_capture_jobs "
            "WHERE source_request_id = ? OR source_message_id = ?",
            (source_request_id, source_message_id),
        ).fetchone()
        return int(row["id"])

    @_synchronized
    def captured_message_ids(self) -> set[int]:
        rows = self._conn.execute(
            "SELECT source_message_id FROM memory_capture_jobs WHERE source_message_id IS NOT NULL"
        )
        return {int(row[0]) for row in rows}

    @_synchronized
    def save_extraction(self, job_id: int, extracted_json: str) -> None:
        self._conn.execute(
            "UPDATE memory_capture_jobs SET extracted_json = ? WHERE id = ?",
            (extracted_json, job_id),
        )
        self._conn.commit()

    @_synchronized
    def claim_next(self, *, stale_after_seconds: int = 600) -> Optional[CaptureJob]:
        now = _now()
        stale_before = (
            datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        ).isoformat()
        self._conn.execute("BEGIN IMMEDIATE")
        row = self._conn.execute(
            "SELECT * FROM memory_capture_jobs WHERE "
            "(status IN (?, ?) AND next_attempt_at <= ?) OR "
            "(status = ? AND updated_at <= ?) ORDER BY id LIMIT 1",
            (
                CaptureJobStatus.PENDING.value,
                CaptureJobStatus.RETRY.value,
                now,
                CaptureJobStatus.PROCESSING.value,
                stale_before,
            ),
        ).fetchone()
        if row is None:
            self._conn.commit()
            return None
        self._conn.execute(
            "UPDATE memory_capture_jobs SET status = ?, attempts = attempts + 1, "
            "updated_at = ? WHERE id = ?",
            (CaptureJobStatus.PROCESSING.value, now, row["id"]),
        )
        self._conn.commit()
        return self.get_job(int(row["id"]))

    @_synchronized
    def mark_succeeded(self, job_id: int) -> None:
        self._conn.execute(
            "UPDATE memory_capture_jobs SET content = NULL, extracted_json = NULL, "
            "status = ?, error_code = NULL, "
            "updated_at = ? WHERE id = ?",
            (CaptureJobStatus.SUCCEEDED.value, _now(), job_id),
        )
        self._conn.commit()

    @_synchronized
    def mark_failed(self, job_id: int, error_code: str, *, max_attempts: int = 5) -> None:
        job = self.get_job(job_id)
        status = (
            CaptureJobStatus.DEAD_LETTER
            if job.attempts >= max_attempts
            else CaptureJobStatus.RETRY
        )
        delay = min(300, 2 ** max(0, job.attempts - 1))
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        self._conn.execute(
            "UPDATE memory_capture_jobs SET status = ?, next_attempt_at = ?, "
            "error_code = ?, updated_at = ? WHERE id = ?",
            (status.value, next_at, error_code, _now(), job_id),
        )
        self._conn.commit()

    @_synchronized
    def retry(self, job_id: int) -> None:
        self._conn.execute(
            "UPDATE memory_capture_jobs SET status = ?, next_attempt_at = ?, "
            "error_code = NULL, updated_at = ? WHERE id = ? AND content IS NOT NULL",
            (CaptureJobStatus.RETRY.value, _now(), _now(), job_id),
        )
        self._conn.commit()

    @_synchronized
    def defer_model(self, job_id: int, error_code: str, seconds: int) -> None:
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        self._conn.execute(
            "UPDATE memory_capture_jobs SET status=?, next_attempt_at=?, error_code=?, "
            "updated_at=?, attempts=MAX(0,attempts-1) WHERE id=? AND status=?",
            (CaptureJobStatus.RETRY.value, next_at, error_code, _now(), job_id,
             CaptureJobStatus.PROCESSING.value),
        )
        self._conn.commit()

    @_synchronized
    def get_job(self, job_id: int) -> CaptureJob:
        row = self._conn.execute(
            "SELECT * FROM memory_capture_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such memory job: {job_id}")
        return self._to_job(row)

    @_synchronized
    def list_jobs(self, *, limit: int = 100, user_id: Optional[str] = None) -> Sequence[CaptureJob]:
        clause = " WHERE user_id = ?" if user_id is not None else ""
        params = (user_id, limit) if user_id is not None else (limit,)
        rows = self._conn.execute(
            "SELECT * FROM memory_capture_jobs" + clause + " ORDER BY id DESC LIMIT ?", params
        )
        return tuple(self._to_job(row) for row in rows)

    @_synchronized
    def queue_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS count FROM memory_capture_jobs GROUP BY status"
        )
        return {str(row["status"]): int(row["count"]) for row in rows}

    @_synchronized
    def mark_snapshot_pending(self) -> None:
        self._conn.execute(
            "UPDATE memory_snapshot_state SET status = 'pending', error_code = NULL, "
            "updated_at = ? WHERE singleton = 1",
            (_now(),),
        )
        self._conn.commit()

    @_synchronized
    def mark_snapshot_succeeded(self, *, generated_at: str, memory_count: int) -> None:
        self._conn.execute(
            "UPDATE memory_snapshot_state SET status = 'current', error_code = NULL, "
            "generated_at = ?, memory_count = ?, updated_at = ? WHERE singleton = 1",
            (generated_at, memory_count, _now()),
        )
        self._conn.commit()

    @_synchronized
    def mark_snapshot_failed(self, error_code: str) -> None:
        self._conn.execute(
            "UPDATE memory_snapshot_state SET status = 'failed', error_code = ?, "
            "updated_at = ? WHERE singleton = 1",
            (error_code, _now()),
        )
        self._conn.commit()

    @_synchronized
    def snapshot_state(self) -> dict[str, object]:
        row = self._conn.execute(
            "SELECT status, error_code, generated_at, memory_count, updated_at "
            "FROM memory_snapshot_state WHERE singleton = 1"
        ).fetchone()
        return dict(row) if row is not None else {"status": "missing"}

    @_synchronized
    def record_change(self, change: NewMemoryChange) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO memory_change_log "
            "(memory_id, user_id, persona_id, scope, action, before_content, "
            "after_content, source_request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                change.memory_id,
                change.user_id,
                change.persona_id,
                change.scope.value,
                change.action.upper(),
                change.before,
                change.after,
                change.source_request_id,
                _now(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def list_changes(
        self, *, user_id: str, memory_id: Optional[str] = None, limit: int = 200
    ) -> Sequence[MemoryChange]:
        sql = "SELECT * FROM memory_change_log WHERE user_id = ?"
        params: list[object] = [user_id]
        if memory_id:
            sql += " AND memory_id = ?"
            params.append(memory_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return tuple(self._to_change(row) for row in self._conn.execute(sql, params))

    @staticmethod
    def _to_job(row: sqlite3.Row) -> CaptureJob:
        return CaptureJob(
            id=int(row["id"]),
            source_request_id=row["source_request_id"],
            user_id=row["user_id"],
            persona_id=row["persona_id"],
            session_id=row["session_id"],
            content=row["content"],
            status=CaptureJobStatus(row["status"]),
            attempts=int(row["attempts"]),
            next_attempt_at=row["next_attempt_at"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            source_message_id=row["source_message_id"],
            source_created_at=row["source_created_at"],
            extracted_json=row["extracted_json"],
        )

    @staticmethod
    def _to_change(row: sqlite3.Row) -> MemoryChange:
        return MemoryChange(
            id=int(row["id"]),
            memory_id=row["memory_id"],
            user_id=row["user_id"],
            persona_id=row["persona_id"],
            scope=MemoryScope(row["scope"]),
            action=row["action"],
            before=row["before_content"],
            after=row["after_content"],
            source_request_id=row["source_request_id"],
            created_at=row["created_at"],
        )
