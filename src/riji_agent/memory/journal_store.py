"""Durable journal provenance, jobs, budgets, relationships and suppression."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from riji_agent.memory.journal_types import (
    JournalEvidence, JournalMemoryError, JournalMemoryPolicy, JournalSource, utc_now,
)

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, version TEXT NOT NULL, kind TEXT NOT NULL,
 observed_at TEXT, status TEXT NOT NULL, reason TEXT, seen TEXT NOT NULL,
 initial INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, token TEXT, lease_until TEXT,
 available_at TEXT NOT NULL, error TEXT, extracted TEXT, decisions TEXT
);
CREATE INDEX IF NOT EXISTS evidence_jobs ON evidence(active, status, available_at);
CREATE INDEX IF NOT EXISTS evidence_source ON evidence(source_id);
CREATE TABLE IF NOT EXISTS budgets (
 key TEXT PRIMARY KEY, chars INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL, action TEXT NOT NULL,
 memory_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content_hash TEXT NOT NULL, kind TEXT NOT NULL,
 valid_from TEXT, series TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'current',
 protected INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS supports (
 memory_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
 PRIMARY KEY(memory_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS relations (
 source_id TEXT NOT NULL, target_id TEXT NOT NULL, kind TEXT NOT NULL,
 PRIMARY KEY(source_id, target_id, kind)
);
CREATE TABLE IF NOT EXISTS suppression (
 kind TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(kind, value)
);
CREATE TABLE IF NOT EXISTS cleanup (
 memory_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, available_at TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS content_aliases (
 memory_id TEXT NOT NULL, content_hash TEXT NOT NULL, PRIMARY KEY(memory_id,content_hash)
);
CREATE TABLE IF NOT EXISTS native_supports (
 memory_id TEXT NOT NULL, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
 source_id TEXT NOT NULL, observed_at TEXT, PRIMARY KEY(memory_id,request_id)
);
CREATE TABLE IF NOT EXISTS egress_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, phase TEXT NOT NULL,
 refs TEXT NOT NULL, request_chars INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS initialization_evidence (
 id TEXT NOT NULL, version TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 PRIMARY KEY(id,version)
);
CREATE TABLE IF NOT EXISTS initialization_seeds (
 id TEXT PRIMARY KEY, version TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', sent_at TEXT, report_json TEXT
);
CREATE TABLE IF NOT EXISTS initialization_recovery (
 id TEXT PRIMARY KEY, version TEXT NOT NULL, requested_at TEXT NOT NULL, sent_at TEXT
);
"""


class JournalMemoryStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        path.chmod(0o600)

    def close(self) -> None:
        self._conn.close()

    def backup(self, destination: Path) -> None:
        with self._lock, sqlite3.connect(destination) as target:
            self._conn.backup(target)
        destination.chmod(0o600)

    def rows(self, sql: str, parameters: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, parameters)]

    def execute(self, sql: str, parameters: tuple = ()) -> None:
        with self._lock, self._conn:
            self._conn.execute(sql, parameters)

    def get_control(self, key: str, default: str = "") -> str:
        rows = self.rows("SELECT value FROM control WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_control(self, key: str, value: str) -> None:
        self.execute("INSERT INTO control VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))

    def bump_epoch(self) -> None:
        self.set_control("catalog_epoch", uuid.uuid4().hex)

    def configure(self, policy: JournalMemoryPolicy) -> None:
        previous = self.get_control("scope")
        owner = self.get_control("user_id")
        if owner and owner != policy.user_id:
            raise JournalMemoryError("journal_memory_owner_change_requires_new_store")
        if self.get_control("initialization_state") == "active" and (
                not policy.initialization_unlimited or self.get_control("initialization_scope") != policy.scope_id):
            self.set_control("initialization_state", "cancelled")
        if previous and previous != policy.scope_id:
            self.execute("UPDATE evidence SET active=0,token=NULL,lease_until=NULL,extracted=NULL,decisions=NULL,"
                         "status=CASE WHEN status='processing' THEN 'pending' ELSE status END")
            self.set_control("initial_scan_complete", "")
            self.set_control("scan_requested", "1")
        self.set_control("scope", policy.scope_id)
        self.set_control("user_id", policy.user_id)
        previous_daily = self.get_control("budget_daily_chars")
        if not previous_daily or int(previous_daily) < policy.daily_chars:
            self.set_control("budget_recheck_requested", "1")
        self.set_control("budget_daily_chars", str(policy.daily_chars))

    def start_initialization(self, policy: JournalMemoryPolicy) -> bool:
        """Freeze one authorized scan; an empty scan does not consume the batch."""
        if (not policy.initialization_unlimited or self.get_control("initialization_state")
                or not self.get_control("initial_scan_complete") or self.get_control("scan_error")):
            return False
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if self._conn.execute("SELECT 1 FROM control WHERE key='initialization_state'").fetchone():
                return False
            self._conn.execute("INSERT OR IGNORE INTO initialization_evidence(id,version,state) "
                "SELECT e.id,e.version,CASE WHEN e.status IN ('succeeded','suppressed') THEN e.status ELSE 'pending' END "
                "FROM evidence e JOIN sources s ON s.id=e.source_id "
                "JOIN privacy_history h ON h.id=e.id AND h.version=e.version WHERE e.active=1 AND s.status='eligible' "
                "AND e.status IN ('pending','processing','budget','succeeded','suppressed')")
            if not self._conn.execute("SELECT 1 FROM initialization_evidence LIMIT 1").fetchone():
                return False
            for key, value in (("initialization_state", "active"), ("initialization_scope", policy.scope_id),
                               ("initialization_started_at", utc_now())):
                self._conn.execute("INSERT INTO control VALUES (?,?)", (key, value))
        self.wake_initialization_budget(policy)
        return True

    def wake_initialization_budget(self, policy: JournalMemoryPolicy) -> None:
        if not self.initialization_active(policy):
            return
        self.execute("UPDATE evidence SET status='pending',error=NULL,available_at=? WHERE active=1 "
            "AND status='budget' AND error='journal_daily_budget' AND EXISTS "
            "(SELECT 1 FROM initialization_evidence i WHERE i.id=evidence.id AND i.version=evidence.version "
            "AND i.state='pending')", (utc_now(),))

    def wake_daily_budget(self) -> None:
        """A larger approved daily allowance can release only daily-budget waits."""
        if self.get_control("budget_recheck_requested") != "1":
            return
        self.execute("UPDATE evidence SET status='pending',error=NULL,available_at=? "
                     "WHERE active=1 AND status='budget' AND error='journal_daily_budget'", (utc_now(),))
        self.set_control("organization_budget_recheck_requested", "1")
        self.set_control("budget_recheck_requested", "0")

    def initialization_active(self, policy: JournalMemoryPolicy) -> bool:
        return (policy.initialization_unlimited and self.get_control("initialization_state") == "active"
                and self.get_control("initialization_scope") == policy.scope_id)

    def initialization_member(self, policy: JournalMemoryPolicy, evidence_id: str, version: str) -> bool:
        return self.initialization_active(policy) and bool(self.rows(
            "SELECT 1 FROM initialization_evidence i JOIN evidence e ON e.id=i.id AND e.version=i.version "
            "WHERE i.id=? AND i.version=? AND i.state='pending' AND e.active=1 "
            "AND e.status IN ('pending','processing','retry','budget')", (evidence_id, version)))

    def track_initialization_seed(self, policy: JournalMemoryPolicy, memory_id: str, version: str) -> None:
        if self.initialization_active(policy):
            self.execute("INSERT OR IGNORE INTO initialization_seeds(id,version) VALUES (?,?)", (memory_id, version))

    def initialization_seed(self, policy: JournalMemoryPolicy, memory_id: str, version: str) -> bool:
        return self.initialization_active(policy) and bool(self.rows(
            "SELECT 1 FROM initialization_seeds WHERE id=? AND version=? AND state='pending'", (memory_id, version)))

    def _initialization_recovery_scope(self, policy: JournalMemoryPolicy) -> bool:
        return (policy.enabled and policy.initialization_unlimited
                and self.get_control("initialization_state") in {"active", "blocked", "completed"}
                and self.get_control("initialization_scope") == policy.scope_id
                and self.get_control("scope") == policy.scope_id)

    def initialization_recovery_member(self, policy: JournalMemoryPolicy, memory_id: str, version: str) -> bool:
        """Only an unspent, explicitly registered retry of this frozen batch qualifies."""
        return self._initialization_recovery_scope(policy) and bool(self.rows(
            "SELECT 1 FROM initialization_seeds s JOIN initialization_recovery r "
            "ON r.id=s.id AND r.version=s.version WHERE s.id=? AND s.version=? "
            "AND s.state='retry_pending' AND s.sent_at<>'' "
            "AND r.requested_at<>'' AND r.sent_at IS NULL", (memory_id, version)))

    def _initialization_recovery_counts(self, policy: JournalMemoryPolicy) -> dict[str, int]:
        if not self._initialization_recovery_scope(policy):
            return {}
        return {row["state"]: row["n"] for row in self.rows(
            "SELECT s.state,COUNT(*) n FROM initialization_seeds s JOIN initialization_recovery r "
            "ON r.id=s.id AND r.version=s.version WHERE s.sent_at<>'' AND r.requested_at<>'' "
            "AND ((s.state='retry_pending' AND r.sent_at IS NULL) "
            "OR (s.state='retry_spent' AND r.sent_at IS NOT NULL)) GROUP BY s.state")}

    def finish_initialization_seed(self, memory_id: str, version: str, state: str,
                                   report: Optional[dict] = None) -> None:
        if state not in {"done", "failed"}:
            raise ValueError("invalid_initialization_seed_state")
        serialized = json.dumps(report) if report is not None else None
        self.execute("UPDATE initialization_seeds SET state=CASE WHEN state='retry_spent' "
                     "THEN 'retry_' || ? ELSE ? END,report_json=CASE WHEN EXISTS "
                     "(SELECT 1 FROM suppression s JOIN json_each(?,'$.versions') v ON s.value=v.key "
                     "WHERE s.kind='memory') THEN NULL ELSE ? END "
                     "WHERE id=? AND version=? AND state IN ('spent','retry_spent')",
                     (state, state, serialized, serialized, memory_id, version))

    def recover_initialization_seeds(self, refs: list[tuple[str, str]]) -> int:
        """Explicitly queue one recovery of exact failed versions; never reopen the batch."""
        queued = 0
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            for memory_id, version in set(refs):
                result = self._conn.execute("INSERT OR IGNORE INTO initialization_recovery "
                    "SELECT id,version,?,NULL FROM initialization_seeds WHERE id=? AND version=? "
                    "AND state='failed'", (utc_now(), memory_id, version))
                if result.rowcount:
                    self._conn.execute("UPDATE initialization_seeds SET state='retry_pending' "
                                       "WHERE id=? AND version=? AND state='failed'", (memory_id, version))
                    queued += 1
        return queued

    def settle_initialization_coverage(self, covered: dict[str, str]) -> None:
        for memory_id, version in covered.items():
            self.execute("UPDATE initialization_seeds SET state=CASE WHEN state='retry_pending' "
                         "THEN 'retry_done' ELSE 'done' END WHERE id=? AND version=? "
                         "AND state IN ('pending','retry_pending')",
                         (memory_id, version))

    def exclude_initialization_seeds(self, versions: dict[str, str]) -> None:
        for seed in self.rows("SELECT id,version FROM initialization_seeds WHERE state IN ('pending','retry_pending')"):
            if versions.get(seed["id"]) != seed["version"]:
                self.execute("UPDATE initialization_seeds SET state='excluded' WHERE id=? "
                             "AND state IN ('pending','retry_pending')", (seed["id"],))

    def refresh_initialization(self, policy: JournalMemoryPolicy, *, organization_allowed: bool) -> None:
        state = self.get_control("initialization_state")
        if (state not in {"active", "blocked", "completed"}
                or self.get_control("initialization_scope") != policy.scope_id):
            return
        if state == "completed" and not self.rows("SELECT 1 FROM initialization_seeds "
                "WHERE state IN ('retry_pending','retry_spent') LIMIT 1"):
            return
        with self._lock, self._conn:
            stale = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            self._conn.execute("UPDATE initialization_seeds SET state='failed' WHERE state='spent' AND sent_at<?", (stale,))
            self._conn.execute("UPDATE initialization_seeds SET state='retry_failed' WHERE state='retry_spent' "
                "AND EXISTS (SELECT 1 FROM initialization_recovery r WHERE r.id=initialization_seeds.id "
                "AND r.version=initialization_seeds.version AND r.sent_at<?)", (stale,))
            self._conn.execute("UPDATE initialization_evidence SET state=CASE "
                "WHEN NOT EXISTS (SELECT 1 FROM evidence e WHERE e.id=initialization_evidence.id "
                "AND e.version=initialization_evidence.version AND e.active=1) THEN 'excluded' "
                "ELSE COALESCE((SELECT CASE WHEN e.status IN ('succeeded','suppressed','failed','source_budget') "
                "THEN e.status ELSE 'pending' END FROM evidence e WHERE e.id=initialization_evidence.id "
                "AND e.version=initialization_evidence.version),'excluded') END WHERE state='pending'")
            if not organization_allowed:
                self._conn.execute("UPDATE initialization_seeds SET state='excluded' "
                                   "WHERE state IN ('pending','retry_pending')")
            pending = self._conn.execute("SELECT 1 FROM initialization_evidence WHERE state='pending' LIMIT 1").fetchone()
            seeds = self._conn.execute("SELECT 1 FROM initialization_seeds "
                "WHERE state IN ('pending','spent','retry_pending','retry_spent') LIMIT 1").fetchone()
            if not pending and not seeds:
                failed = self._conn.execute("SELECT 1 FROM initialization_evidence WHERE state IN ('failed','source_budget') "
                    "UNION ALL SELECT 1 FROM initialization_seeds WHERE state IN ('failed','retry_failed') LIMIT 1").fetchone()
                self._conn.execute("UPDATE control SET value=? WHERE key='initialization_state'", ("blocked" if failed else "completed",))
                self._conn.execute("INSERT OR REPLACE INTO control VALUES ('initialization_finished_at',?)", (utc_now(),))

    def initialization_status(self, policy: JournalMemoryPolicy) -> dict[str, Any]:
        states = {row["state"]: row["n"] for row in self.rows("SELECT state,COUNT(*) n FROM initialization_evidence GROUP BY state")}
        seeds = {row["state"]: row["n"] for row in self.rows("SELECT state,COUNT(*) n FROM initialization_seeds GROUP BY state")}
        chars = self.rows("SELECT COALESCE(SUM(chars),0) n FROM budgets WHERE key LIKE 'initialization:%'")[0]["n"]
        state = self.get_control("initialization_state") or "awaiting_scan"
        recovery = self._initialization_recovery_counts(policy)
        active = self.initialization_active(policy)
        recovery_active = bool(sum(recovery.values()))
        return {"enabled": policy.initialization_unlimited, "state": state if policy.initialization_unlimited else "disabled",
                "active": active, "total": sum(states.values()),
                "pending": states.get("pending", 0), "completed": states.get("succeeded", 0),
                "excluded": states.get("excluded", 0) + states.get("suppressed", 0),
                "failed": states.get("failed", 0) + states.get("source_budget", 0),
                "organization": seeds, "request_chars": chars,
                "recovery_pending": recovery.get("retry_pending", 0),
                "recovery_spent": recovery.get("retry_spent", 0), "recovery_active": recovery_active,
                "exemption_active": active or recovery_active}

    def record_source(self, source: JournalSource, generation: str) -> None:
        initial = int(not self.get_control("initial_scan_complete"))
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "path=excluded.path,version=excluded.version,kind=excluded.kind,"
                "observed_at=excluded.observed_at,status=excluded.status,reason=excluded.reason,"
                "seen=excluded.seen,updated_at=excluded.updated_at",
                (source.id, source.path, source.version, source.kind, source.observed_at,
                 source.status, source.reason, generation, initial, utc_now()),
            )
            ids = {item.id for item in source.evidence}
            for row in self._conn.execute("SELECT id FROM evidence WHERE source_id=?", (source.id,)):
                if row["id"] not in ids:
                    self._conn.execute("UPDATE evidence SET active=0,token=NULL,extracted=NULL,"
                                       "decisions=NULL,payload=json_remove(payload,'$.text') WHERE id=?", (row["id"],))
            for item in source.evidence:
                self._upsert_evidence(item)

    def _upsert_evidence(self, evidence: JournalEvidence) -> None:
        current = self._conn.execute("SELECT * FROM evidence WHERE id=?", (evidence.id,)).fetchone()
        payload = evidence.to_dict()
        payload.pop("text", None)
        if current:
            changed = current["version"] != evidence.version
            reset = (changed or not current["active"] or (current["status"] == "processing" and not current["token"]))
            reset = reset and current["status"] not in {"succeeded", "suppressed"}
            self._conn.execute(
                "UPDATE evidence SET version=?,payload=?,active=1,status=?,token=?,lease_until=?,"
                "attempts=?,available_at=?,extracted=?,decisions=? WHERE id=?",
                (evidence.version, json.dumps(payload, ensure_ascii=False),
                 "pending" if reset else current["status"], None if reset else current["token"],
                 None if reset else current["lease_until"], 0 if reset else current["attempts"],
                 utc_now() if reset else current["available_at"], None if reset else current["extracted"],
                 None if reset else current["decisions"], evidence.id),
            )
        else:
            self._conn.execute("INSERT INTO evidence (id,source_id,version,payload,available_at) VALUES (?,?,?,?,?)",
                               (evidence.id, evidence.source_id, evidence.version,
                                json.dumps(payload, ensure_ascii=False), utc_now()))

    def finish_scan(self, generation: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE sources SET status='deleted',reason='source_missing' WHERE seen<>?", (generation,))
            self._conn.execute("UPDATE evidence SET active=0,token=NULL,extracted=NULL,decisions=NULL,"
                               "payload=json_remove(payload,'$.text') WHERE source_id IN "
                               "(SELECT id FROM sources WHERE status='deleted')")
        self.set_control("initial_scan_complete", "1")
        self.set_control("last_scan", utc_now())
        self.set_control("scan_error", "")

    def claim(self, *, history: bool = True, incremental: bool = True) -> Optional[dict[str, Any]]:
        if self.get_control("paused") == "1":
            return None
        now = utc_now()
        until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute("UPDATE evidence SET status='retry',token=NULL WHERE status='processing' "
                               "AND lease_until<?", (now,))
            row = self._conn.execute(
                "SELECT e.* FROM evidence e JOIN sources s ON s.id=e.source_id WHERE e.active=1 "
                "AND s.status='eligible' AND e.status IN ('pending','retry','budget') AND e.available_at<=? "
                "AND ((? AND EXISTS(SELECT 1 FROM privacy_history h WHERE h.id=e.id AND h.version=e.version)) "
                "OR (? AND NOT EXISTS(SELECT 1 FROM privacy_history h WHERE h.id=e.id AND h.version=e.version))) "
                "ORDER BY COALESCE(s.observed_at,'9999'),s.path,e.id LIMIT 1", (now, history, incremental),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self._conn.execute("UPDATE evidence SET status='processing',token=?,lease_until=?,attempts=attempts+1 "
                               "WHERE id=?", (token, until, row["id"]))
            return dict(row, token=token, attempts=row["attempts"] + 1)

    def check_job(self, job: dict[str, Any]) -> None:
        rows = self.rows("SELECT token,active FROM evidence WHERE id=?", (job["id"],))
        if self.get_control("paused") == "1":
            raise JournalMemoryError("journal_memory_paused")
        if not rows or not rows[0]["active"] or rows[0]["token"] != job["token"]:
            raise JournalMemoryError("journal_source_changed")

    def cache(self, job: dict[str, Any], phase: str, value: Any) -> None:
        if phase not in {"extracted", "decisions"}:
            raise ValueError("invalid_phase")
        self.check_job(job)
        self.execute(f"UPDATE evidence SET {phase}=? WHERE id=? AND token=?",
                     (json.dumps(value, ensure_ascii=False) if value is not None else None,
                      job["id"], job["token"]))

    def succeed(self, job: dict[str, Any]) -> None:
        self.check_job(job)
        with self._lock, self._conn:
            self._conn.execute("UPDATE evidence SET status='succeeded',extracted=NULL,decisions=NULL,token=NULL,"
                     "lease_until=NULL,error=NULL,payload=json_remove(payload,'$.text') WHERE id=? AND token=?",
                     (job["id"], job["token"]))
            self._conn.execute("UPDATE initialization_evidence SET state='succeeded' WHERE id=? AND version=? "
                "AND state IN ('failed','source_budget') AND EXISTS (SELECT 1 FROM evidence e "
                "WHERE e.id=initialization_evidence.id AND e.version=initialization_evidence.version "
                "AND e.active=1 AND e.status='succeeded')", (job["id"], job["version"]))

    def fail(self, job: dict[str, Any], code: str, max_attempts: int) -> None:
        status = "failed" if job["attempts"] >= max_attempts else "retry"
        after = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** job["attempts"]))
        if code in {"journal_daily_budget", "journal_source_budget"}:
            status = "budget" if code == "journal_daily_budget" else "source_budget"
            after = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        if code in {"journal_memory_paused", "journal_source_changed", "journal_consent_required"}:
            status, after = "pending", datetime.now(timezone.utc)
        self.execute("UPDATE evidence SET status=?,error=?,available_at=?,token=NULL,lease_until=NULL "
                     "WHERE id=? AND token=?", (status, code, after.isoformat(), job["id"], job["token"]))

    def retry(self) -> None:
        self.execute("UPDATE evidence SET status='pending',attempts=0,error=NULL,available_at=? "
                     "WHERE active=1 AND status IN ('failed','budget','source_budget','retry')", (utc_now(),))
        self.execute("UPDATE cleanup SET attempts=0,available_at=? WHERE status='pending'", (utc_now(),))

    def retry_failed_evidence(self, refs: list[tuple[str, str]]) -> int:
        """Retry selected live versions without resetting caches or budget ledgers."""
        queued = 0
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            for evidence_id, version in set(refs):
                queued += self._conn.execute("UPDATE evidence SET status='pending',attempts=0,error=NULL,"
                    "available_at=?,token=NULL,lease_until=NULL WHERE id=? AND version=? AND active=1 "
                    "AND status='failed' AND EXISTS (SELECT 1 FROM sources s WHERE s.id=evidence.source_id "
                    "AND s.version=evidence.version AND s.status='eligible')",
                    (utc_now(), evidence_id, version)).rowcount
        return queued

    def defer_model(self, job: dict[str, Any], code: str, seconds: int) -> None:
        after = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        self.execute("UPDATE evidence SET status='retry',error=?,available_at=?,token=NULL,"
                     "lease_until=NULL,attempts=MAX(0,attempts-1) WHERE id=? AND token=?",
                     (code, after, job["id"], job["token"]))

    def reserve_budget(self, policy: JournalMemoryPolicy, evidence: JournalEvidence,
                       request_chars: int) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            initialization = self.initialization_member(policy, evidence.id, evidence.version)
            keys = [("initialization:" + day if initialization else "day:" + day, request_chars,
                     None if initialization else policy.daily_chars, "journal_daily_budget"),
                    ("source:" + evidence.source_id + ":" + evidence.version,
                     len(evidence.text), policy.source_chars, "journal_source_budget")]
            for key, amount, maximum, code in keys:
                row = self._conn.execute("SELECT chars FROM budgets WHERE key=?", (key,)).fetchone()
                if maximum is not None and (row[0] if row else 0) + amount > maximum:
                    raise JournalMemoryError(code)
            for key, amount, _, _ in keys:
                self._conn.execute("INSERT INTO budgets VALUES (?,?) ON CONFLICT(key) DO UPDATE "
                                   "SET chars=chars+excluded.chars", (key, amount))

    def evidence(self, evidence_id: str) -> Optional[dict[str, Any]]:
        rows = self.rows("SELECT * FROM evidence WHERE id=?", (evidence_id,))
        return rows[0] if rows else None

    def record(self, memory_id: str) -> Optional[dict[str, Any]]:
        rows = self.rows("SELECT * FROM records WHERE id=?", (memory_id,))
        return rows[0] if rows else None

    def register(self, record: dict[str, Any], evidence_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,?,?,?)",
                               tuple(record[key] for key in ("id", "user_id", "content_hash", "kind",
                                     "valid_from", "series", "state", "protected", "created_at")))
            if evidence_id:
                self._conn.execute("INSERT OR IGNORE INTO supports VALUES (?,?)", (record["id"], evidence_id))
            self._conn.execute("INSERT OR IGNORE INTO content_aliases VALUES (?,?)", (record["id"], record["content_hash"]))

    def link(self, memory_id: str, target_id: str, kind: str) -> None:
        self.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?)", (memory_id, target_id, kind))

    def operation(self, operation_id: str) -> Optional[dict[str, Any]]:
        rows = self.rows("SELECT * FROM operations WHERE id=?", (operation_id,))
        return rows[0] if rows else None

    def begin_operation(self, operation_id: str, evidence_id: str, action: str) -> None:
        self.execute("INSERT OR IGNORE INTO operations (id,evidence_id,action,created_at) VALUES (?,?,?,?)",
                     (operation_id, evidence_id, action, utc_now()))

    def finish_operation(self, operation_id: str, memory_id: str) -> None:
        self.execute("UPDATE operations SET status='applied',memory_id=? WHERE id=?", (memory_id, operation_id))

    def suppressed(self, *, evidence_id: str = "", content_hash: str = "", memory_id: str = "") -> bool:
        pairs = [("evidence", evidence_id), ("content", content_hash), ("memory", memory_id)]
        return any(self.rows("SELECT 1 FROM suppression WHERE kind=? AND value=?", pair)
                   for pair in pairs if pair[1])

    def suppress(self, memory_id: str, content_hash: str) -> None:
        refs = self.rows("SELECT evidence_id FROM supports WHERE memory_id=?", (memory_id,))
        pairs = [("memory", memory_id), ("content", content_hash)]
        pairs.extend(("content", row["content_hash"]) for row in
                     self.rows("SELECT content_hash FROM content_aliases WHERE memory_id=?", (memory_id,)))
        pairs.extend(("evidence", row["evidence_id"]) for row in refs)
        with self._lock, self._conn:
            for kind, value in pairs:
                self._conn.execute("INSERT OR IGNORE INTO suppression VALUES (?,?,?)", (kind, value, utc_now()))
            # A cached group may mention this memory as context for any seed.
            self._conn.execute("UPDATE initialization_seeds SET report_json=NULL")
            self._conn.execute("UPDATE evidence SET decisions=NULL")
            self._conn.execute("UPDATE records SET state='deleted' WHERE id=?", (memory_id,))
            self._conn.execute("INSERT OR IGNORE INTO cleanup(memory_id,available_at) VALUES (?,?)", (memory_id, utc_now()))
            for row in refs:
                self._conn.execute("UPDATE evidence SET extracted=NULL,decisions=NULL,token=NULL,"
                                   "payload=json_remove(payload,'$.text'),status='suppressed' WHERE id=?",
                                   (row["evidence_id"],))

    def progress(self) -> dict[str, Any]:
        sources = self.rows("SELECT * FROM sources ORDER BY path")
        jobs = self.rows("SELECT source_id,status,error,COUNT(*) AS count FROM evidence WHERE active=1 "
                         "GROUP BY source_id,status,error")
        for source in sources:
            source["jobs"] = [row for row in jobs if row["source_id"] == source["id"]]
            if source["status"] == "eligible":
                states = {row["status"] for row in source["jobs"]}
                source["status"] = "completed" if states <= {"succeeded", "suppressed"} else "pending"
            counts = self.rows("SELECT COUNT(DISTINCT o.memory_id) AS count FROM operations o JOIN evidence e "
                               "ON o.evidence_id=e.id WHERE e.source_id=? AND e.active=1 AND o.status='applied' "
                               "AND o.memory_id IS NOT NULL AND o.memory_id<>''", (source["id"],))
            source["memory_count"] = counts[0]["count"]
            source["outcome"] = "no_durable_memory" if source["status"] == "completed" and not counts[0]["count"] else None
        pending = sum(s["status"] in {"pending", "failed"} for s in sources)
        done = sum(s["status"] == "completed" for s in sources)
        return {"sources": sources, "discovered": len(sources), "completed": done,
                "pending": pending, "paused": self.get_control("paused") == "1",
                "last_scan": self.get_control("last_scan"), "error": self.get_control("scan_error"),
                "initialized": bool(self.get_control("initial_scan_complete")) and not pending
                and not self.get_control("scan_error"),
                "cleanup_pending": len(self.rows("SELECT memory_id FROM cleanup WHERE status='pending'")),
                "budgets": self.rows("SELECT * FROM budgets WHERE key LIKE 'day:%' ORDER BY key DESC LIMIT 7")}

    def reserve_daily(self, policy: JournalMemoryPolicy, amount: int, *,
                      initialization_seed: Optional[tuple[str, str]] = None) -> None:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            recovery = initialization_seed is not None and self._recovery_for_send(initialization_seed)
            initial_seed = initialization_seed is not None and self.initialization_seed(policy, *initialization_seed)
            initialization = initial_seed or (recovery and self.initialization_recovery_member(policy, *initialization_seed))
            key = ("initialization:" if initialization else "day:") + datetime.now(timezone.utc).date().isoformat()
            row = self._conn.execute("SELECT chars FROM budgets WHERE key=?", (key,)).fetchone()
            if not initialization and (row[0] if row else 0) + amount > policy.daily_chars:
                raise JournalMemoryError("journal_daily_budget")
            self._conn.execute("INSERT INTO budgets VALUES (?,?) ON CONFLICT(key) DO UPDATE "
                               "SET chars=chars+excluded.chars", (key, amount))
            if initial_seed:
                self._conn.execute("UPDATE initialization_seeds SET state='spent',sent_at=? "
                                   "WHERE id=? AND version=? AND state='pending'", (utc_now(), *initialization_seed))
            elif recovery:
                self._conn.execute("UPDATE initialization_seeds SET state='retry_spent' "
                    "WHERE id=? AND version=? AND state='retry_pending'", initialization_seed)
                self._conn.execute("UPDATE initialization_recovery SET sent_at=? WHERE id=? AND version=?",
                                   (utc_now(), *initialization_seed))

    def _recovery_for_send(self, ref: tuple[str, str]) -> bool:
        """Claim checks run in the same transaction as charging either ledger."""
        recovery = self._conn.execute(
            "SELECT requested_at,sent_at FROM initialization_recovery WHERE id=? AND version=?", ref).fetchone()
        seed = self._conn.execute(
            "SELECT state,sent_at FROM initialization_seeds WHERE id=? AND version=?", ref).fetchone()
        if recovery is None:
            if seed is not None and seed["state"].startswith("retry_"):
                raise JournalMemoryError("journal_recovery_invalid")
            return False
        if recovery["sent_at"] is not None or (seed is not None and seed["state"] != "retry_pending"):
            raise JournalMemoryError("journal_recovery_already_sent")
        if seed is None or not seed["sent_at"] or not recovery["requested_at"]:
            raise JournalMemoryError("journal_recovery_invalid")
        return True

    def log_call(self, phase: str, refs: list[str], request_chars: int) -> None:
        self.execute("INSERT INTO egress_attempts(scope,phase,refs,request_chars,created_at) VALUES (?,?,?,?,?)",
                     (self.get_control("scope"), phase, json.dumps(refs), request_chars, utc_now()))
