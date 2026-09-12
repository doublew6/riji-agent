"""Persistent shared budgets, including uncertain attempts and source egress."""

from __future__ import annotations

import time
from typing import Callable

from riji_agent.mentors.models import Conversation, MentorError, Source
from riji_agent.mentors.store import MentorStore


class BudgetService:
    def __init__(self, store: MentorStore, now: Callable[[], float] = time.time) -> None:
        self.store, self.now = store, now

    @staticmethod
    def identifier(conversation: Conversation) -> str:
        # Legacy initial runs retain their existing ledger. Explicit subsequent
        # full runs and followups have independent immutable ledger identifiers.
        return conversation.run_id if conversation.run_kind != "initial" else conversation.id

    def create(self, db, conversation: Conversation) -> None:
        single = conversation.kind == "private" or conversation.run_kind == "followup"
        db.execute("INSERT OR IGNORE INTO mentor_budgets(id,conversation_id,request_limit,time_limit) VALUES (?,?,?,?)",
                   (self.identifier(conversation), conversation.id, 7 if single else 24, 120 if single else 600))

    def activate(self, db, conversation: Conversation) -> None:
        self.create(db, conversation)
        db.execute("UPDATE mentor_budgets SET active_since=? WHERE id=? AND active_since=0",
                   (self.now(), self.identifier(conversation)))

    def pause(self, db, conversation: Conversation) -> None:
        db.execute("UPDATE mentor_budgets SET elapsed=elapsed+MAX(0,?-active_since),active_since=0 "
                   "WHERE id=? AND active_since>0", (self.now(), self.identifier(conversation)))

    def charge(self, conversation: Conversation, sources: tuple[Source, ...], *, final: bool = False) -> None:
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM mentor_budgets WHERE id=?", (self.identifier(conversation),)).fetchone()
            if row is None:
                raise MentorError("budget_missing")
            elapsed = row["elapsed"] + (max(0, self.now() - row["active_since"]) if row["active_since"] else 0)
            reserve = 0 if final or conversation.kind == "private" or conversation.run_kind == "followup" else 1
            if row["requests"] >= row["request_limit"] - reserve or elapsed >= row["time_limit"]:
                raise MentorError("budget_exhausted")
            self._charge_sources(db, conversation, sources)
            db.execute("UPDATE mentor_budgets SET requests=requests+1 WHERE id=?", (row["id"],))

    def _charge_sources(self, db, conversation: Conversation, sources: tuple[Source, ...]) -> None:
        for source in sources:
            if source.kind != "journal":
                continue
            if len(source.text) > 900:
                raise MentorError("source_snippet_limit")
            args = (conversation.owner_id, source.id, source.version)
            row = db.execute("SELECT chars FROM mentor_source_usage WHERE owner=? AND source_id=? AND version=?", args).fetchone()
            if (row[0] if row else 0) + len(source.text) > 4000:
                raise MentorError("source_budget_exhausted")
            db.execute("INSERT INTO mentor_source_usage VALUES (?,?,?,?) ON CONFLICT(owner,source_id,version) "
                       "DO UPDATE SET chars=chars+excluded.chars", (*args, len(source.text)))

    def status(self, conversation: Conversation) -> dict:
        with self.store.transaction() as db:
            rows = db.execute("SELECT id,requests,request_limit,elapsed,time_limit FROM mentor_budgets WHERE conversation_id=?",
                              (conversation.id,)).fetchall()
            values = [dict(row) for row in rows]
            return {"total_requests": sum(row["requests"] for row in rows), "budgets": values,
                    "current_run_id": conversation.run_id,
                    "current": next((row for row in values if row["id"] == self.identifier(conversation)), None)}

    def check_time(self, conversation: Conversation) -> None:
        with self.store.transaction() as db:
            self.check_time_in_transaction(db, conversation)

    def check_time_in_transaction(self, db, conversation: Conversation) -> None:
        row = db.execute("SELECT * FROM mentor_budgets WHERE id=?", (self.identifier(conversation),)).fetchone()
        if row is None or not row["active_since"]:
            raise MentorError("budget_exhausted")
        if row["elapsed"] + max(0, self.now() - row["active_since"]) >= row["time_limit"]:
            raise MentorError("budget_exhausted")
