"""Durable requests and versioned reports for non-destructive memory organization."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


ORGANIZATION_PROVIDER_ERROR_CODES = frozenset({
    "codex_home_not_isolated", "codex_home_permissions_invalid",
    "codex_unsupported_version", "codex_timeout", "codex_unavailable",
    "codex_login_required", "codex_quota_exhausted", "codex_queue_timeout",
    "codex_invalid_response", "codex_request_too_large", "codex_request_failed",
    "codex_response_too_large", "codex_invalid_protocol", "codex_unexpected_tool_activity",
})
ORGANIZATION_ERROR_CODES = ORGANIZATION_PROVIDER_ERROR_CODES | frozenset({
    "organization_failed", "organization_invalid_json", "organization_invalid_report",
    "organization_incomplete_report", "organization_invalid_entry", "organization_invalid_evidence",
    "organization_invalid_category", "organization_invalid_comparison", "organization_invalid_text",
    "organization_invalid_observations", "organization_invalid_observation_evidence",
    "organization_insufficient_independent_evidence",
})


class OrganizationStore:
    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_organization_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                report_json TEXT,
                error_code TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_organization_pending
                ON memory_organization_runs(user_id) WHERE status = 'pending';
            CREATE INDEX IF NOT EXISTS idx_organization_user
                ON memory_organization_runs(user_id, id DESC);
        """)
        self._conn.commit()
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(memory_organization_runs)")}
        if "available_at" not in columns:
            self._conn.execute("ALTER TABLE memory_organization_runs ADD COLUMN available_at TEXT NOT NULL DEFAULT ''")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def clear_user(self, user_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM memory_organization_runs WHERE user_id=?", (user_id,))

    def request(self, user_id: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_organization_runs "
                "(user_id, status, created_at, updated_at) VALUES (?, 'pending', ?, ?)",
                (user_id, now, now),
            )
            row = self._conn.execute(
                "SELECT id FROM memory_organization_runs WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            ).fetchone()
            return int(row["id"])

    def request_if_idle(self, user_id: str) -> Optional[int]:
        """Repair a missing request without waking backoff or duplicating a worker."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            active = self._conn.execute(
                "SELECT 1 FROM memory_organization_runs "
                "WHERE user_id=? AND status IN ('pending','processing') LIMIT 1",
                (user_id,),
            ).fetchone()
            if active:
                return None
            cursor = self._conn.execute(
                "INSERT INTO memory_organization_runs (user_id,status,created_at,updated_at) "
                "VALUES (?,'pending',?,?)", (user_id, now, now),
            )
            return int(cursor.lastrowid)

    def claim(self) -> Optional[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(minutes=15)).isoformat()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            # A stopped process must not leave a permanent 'processing' badge.
            self._conn.execute(
                "UPDATE memory_organization_runs SET status = 'failed', "
                "error_code = 'organization_interrupted', updated_at = ? "
                "WHERE status = 'processing' AND updated_at < ?",
                (now.isoformat(), stale),
            )
            row = self._conn.execute(
                "SELECT * FROM memory_organization_runs r WHERE status='pending' AND available_at<=? "
                "AND NOT EXISTS (SELECT 1 FROM memory_organization_runs active "
                "WHERE active.user_id=r.user_id AND active.status='processing') ORDER BY id LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE memory_organization_runs SET status = 'processing', updated_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
            return dict(row)

    def wake_daily_budget(self, user_id: str) -> None:
        """Release only the user's daily-budget wait after a policy change."""
        with self._lock, self._conn:
            self._conn.execute("UPDATE memory_organization_runs SET available_at=?,error_code=NULL "
                "WHERE user_id=? AND status='pending' AND error_code='journal_daily_budget'",
                (datetime.now(timezone.utc).isoformat(), user_id))

    def defer(self, run_id: int, seconds: int, code: str) -> None:
        after = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self._lock, self._conn:
            row = self._conn.execute("SELECT user_id FROM memory_organization_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return
            pending = self._conn.execute("SELECT id FROM memory_organization_runs WHERE user_id=? AND status='pending'",
                                         (row["user_id"],)).fetchone()
            if pending:
                self._conn.execute("UPDATE memory_organization_runs SET error_code=?,"
                                   "available_at=MAX(available_at,?) WHERE id=?",
                                   (code, after, pending["id"]))
            self._conn.execute("UPDATE memory_organization_runs SET status=?,error_code=?,available_at=? WHERE id=?",
                               ("failed" if pending else "pending", code, after, run_id))

    def finish(self, run_id: int, report: Optional[dict[str, Any]], *, error_code: Optional[str] = None) -> None:
        # This boundary must never persist exception text or model output as a diagnostic.
        safe_code = error_code if type(error_code) is str and error_code in ORGANIZATION_ERROR_CODES else "organization_failed"
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memory_organization_runs SET status = ?, report_json = COALESCE(?, report_json), "
                "error_code = ?, updated_at = ? WHERE id = ?",
                (
                    "ready" if report is not None else "failed",
                    json.dumps(report, ensure_ascii=False) if report is not None else None,
                    None if report is not None else safe_code,
                    datetime.now(timezone.utc).isoformat(), run_id,
                ),
            )

    def record_input(self, run_id: int, manifest: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memory_organization_runs SET report_json = ? WHERE id = ? AND status = 'processing'",
                (json.dumps(manifest, ensure_ascii=False), run_id),
            )

    def latest(self, user_id: str, *, ready_only: bool = False) -> Optional[dict[str, Any]]:
        clause = " AND status = 'ready'" if ready_only else ""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_organization_runs WHERE user_id = ?" + clause + " ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["report"] = json.loads(result.pop("report_json") or "null")
        return result
