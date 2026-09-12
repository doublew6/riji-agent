"""Durable, purpose-specific consent bound to the actual processing configuration."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from riji_agent.memory.journal_types import JournalMemoryError, fingerprint, utc_now

PURPOSES = ("history", "incremental", "organization", "recall")


class JournalPrivacy:
    def __init__(self, engine: Any) -> None:
        self.engine, self.store = engine, engine.store
        self.store.execute("CREATE TABLE IF NOT EXISTS privacy_events (id INTEGER PRIMARY KEY, "
                           "action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.store.execute("CREATE TABLE IF NOT EXISTS privacy_history (id TEXT PRIMARY KEY, version TEXT NOT NULL)")

    @property
    def binding(self) -> str:
        policy = asdict(self.engine.policy)
        policy["root"] = str(policy["root"])
        return fingerprint(json.dumps(policy, sort_keys=True, ensure_ascii=False))

    def status(self) -> dict[str, Any]:
        raw = self.store.get_control("privacy_consent")
        try:
            consent = json.loads(raw) if raw else {}
        except ValueError:
            consent = {}
        if not isinstance(consent, dict):
            consent = {}
        valid = consent.get("binding") == self.binding
        return {"valid": valid, "binding": self.binding, "granted_at": consent.get("granted_at"),
                "permissions": {key: valid and consent.get(key) is True for key in PURPOSES},
                "initialization": self.engine.initialization_status()}

    def grant(self, binding: str, permissions: dict[str, bool]) -> None:
        if binding != self.binding or set(permissions) != set(PURPOSES) or any(
                type(value) is not bool for value in permissions.values()):
            raise JournalMemoryError("privacy_scope_changed_or_invalid")
        if self.store.get_control("restore_in_progress"):
            raise JournalMemoryError("memory_restore_in_progress")
        policy = self.engine.policy
        payload = dict(permissions, binding=binding, granted_at=utc_now(),
                       extraction_host=urlsplit(policy.extraction_destination).hostname,
                       extraction_provider=policy.extraction_provider,
                       extraction_model=policy.extraction_model,
                       recall_host=urlsplit(policy.recall_destination).hostname,
                       recall_provider=policy.recall_provider,
                       recall_model=policy.recall_model,
                       daily_chars=policy.daily_chars, initialization_unlimited=policy.initialization_unlimited,
                       sections=list(policy.sections), date_from=policy.date_from, date_to=policy.date_to)
        with self.store._lock, self.store._conn:
            conn = self.store._conn
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM privacy_history")
            conn.execute("INSERT INTO privacy_history SELECT id,version FROM evidence WHERE active=1")
            conn.execute("INSERT INTO control VALUES ('privacy_consent',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(payload),))
            event = conn.execute("INSERT INTO privacy_events(action,payload,created_at) VALUES (?,?,?)",
                                 ("grant", json.dumps(payload), utc_now())).lastrowid
        self.engine.advance_initialization()
        payload["initialization"] = self.engine.initialization_status()
        self.store.execute("UPDATE privacy_events SET payload=? WHERE id=?", (json.dumps(payload), event))
        self.store.wake_initialization_budget(policy)
        self.store.wake_daily_budget()
        self.engine.notify_change()

    def revoke(self) -> None:
        self.store.set_control("paused", "1")
        self.store.set_control("privacy_consent", "")
        self.store.execute("INSERT INTO privacy_events(action,payload,created_at) VALUES (?,?,?)",
                           ("revoke", "{}", utc_now()))
        self.engine.notify_change()

    def allows(self, purpose: str) -> bool:
        return self.status()["permissions"].get(purpose, False)

    def check(self, purpose: str, job: dict | None = None) -> None:
        if job is not None:
            historical = self.store.rows("SELECT 1 FROM privacy_history WHERE id=? AND version=?",
                                         (job["id"], job["version"]))
            purpose = "history" if historical else "incremental"
        if not self.allows(purpose):
            raise JournalMemoryError("journal_consent_required")

    def claim(self) -> dict | None:
        permissions = self.status()["permissions"]
        if not permissions["history"] and not permissions["incremental"]:
            return None
        return self.store.claim(history=permissions["history"], incremental=permissions["incremental"])
