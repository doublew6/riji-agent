"""Explicit two-channel linking without copying credentials or memory owners."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.models import Application, Envelope, MentorError, Principal, Record
from riji_agent.mentors.store import key


class IdentityLink(Record):
    id: str
    owner_id: str
    application_id: str
    expires_at: float
    status: Literal["pending", "claimed", "confirmed", "cancelled"] = "pending"
    external_user_id: str = ""
    subject: str = ""
    chat_id: str = ""
    proof_hash: str = ""
    failures: int = Field(default=0, ge=0)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class IdentityLinks:
    def __init__(self, identity: IdentityService, applications: set[str],
                 clock: Callable[[], float] = time.time) -> None:
        self.identity, self.store = identity, identity.store
        self.applications, self.clock = frozenset(applications), clock

    def _application(self, db: sqlite3.Connection, identifier: str) -> Application:
        app = self.store.get(db, "application", identifier, Application)
        if identifier not in self.applications or app is None or app.platform != "feishu":
            raise MentorError("identity_link_application_unavailable")
        return app

    def connections(self, owner_id: str) -> dict:
        from riji_agent.mentors.models import ChatBinding
        chats = self.store.list("chat", owner_id, ChatBinding)
        apps = self.store.list("application", "", Application)
        return {"items": [{"application_id": app.id, "persona_id": app.persona_id,
            "connected": any(chat.application_id == app.id and chat.chat_type == "p2p" for chat in chats)}
            for app in apps if app.id in self.applications and app.platform == "feishu"]}

    def create(self, owner_id: str, application_id: str) -> dict:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.store.transaction() as db:
            self._application(db, application_id)
            if self.store.get(db, "principal", owner_id, Principal) is None:
                raise MentorError("identity_link_owner_unavailable")
            # Only pending requests remain sensitive; replacement invalidates them.
            for old in self.store.records(db, "identity_link", owner_id, IdentityLink):
                if old.application_id == application_id and old.status in {"pending", "claimed"}:
                    self._save(db, old.model_copy(update={"status": "cancelled", "proof_hash": ""}))
            link = IdentityLink(id=digest(token), owner_id=owner_id,
                                application_id=application_id, expires_at=now + 600)
            self._save(db, link)
        return {"id": link.id, "command": "/绑定 " + token, "expires_at": link.expires_at}

    def claim(self, application_id: str, message: Envelope) -> dict:
        if message.chat_type != "p2p" or message.sender_kind != "user":
            raise MentorError("identity_link_private_user_required")
        parts = message.text.strip().split()
        if len(parts) != 2 or parts[0] != "/绑定" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parts[1]):
            raise MentorError("identity_link_invalid")
        token = parts[1]
        with self.store.transaction() as db:
            self._application(db, application_id)
            link = self.store.get(db, "identity_link", digest(token), IdentityLink)
            self._claimable(link, application_id)
            claimant = (message.external_user_id, message.subject, message.external_chat_id)
            if link.status == "claimed" and claimant != (link.external_user_id, link.subject, link.chat_id):
                raise MentorError("identity_link_invalid")
            proof = self._proof(token, claimant)
            self._save(db, link.model_copy(update={"status": "claimed", "external_user_id": claimant[0],
                "subject": claimant[1], "chat_id": claimant[2], "proof_hash": digest(proof)}))
        return {"status": "identity_link_claimed", "text": "核对码：" + proof
                + "\n请回到刚才发起绑定的网页，输入此码并确认。确认前不会开放你的记录。"}

    def _claimable(self, link: IdentityLink | None, application_id: str) -> None:
        if (link is None or link.application_id != application_id or link.expires_at <= self.clock()
                or link.status not in {"pending", "claimed"} or link.failures >= 5):
            raise MentorError("identity_link_invalid")

    @staticmethod
    def _proof(token: str, claimant: tuple[str, ...]) -> str:
        raw = hmac.new(token.encode(), key(*claimant).encode(), hashlib.sha256).digest()
        return f"{int.from_bytes(raw[:8], 'big') % 100_000_000:08d}"

    def _owned(self, db: sqlite3.Connection, identifier: str, owner_id: str) -> IdentityLink:
        link = self.store.get(db, "identity_link", identifier, IdentityLink)
        if link is None or link.owner_id != owner_id:
            raise MentorError("identity_link_unavailable")
        return link

    def confirm(self, identifier: str, owner_id: str, proof: str) -> dict:
        failed = False
        with self.store.transaction() as db:
            link = self._owned(db, identifier, owner_id)
            app = self._application(db, link.application_id)
            if link.status == "confirmed":
                return {"status": "connected"}
            self._claimable(link, app.id)
            if link.status != "claimed":
                raise MentorError("identity_link_not_claimed")
            if not secrets.compare_digest(link.proof_hash, digest(proof)):
                self._save(db, link.model_copy(update={"failures": link.failures + 1}))
                failed = True
            else:
                self.identity.link_verified_account(db, app, link)
                self._save(db, link.model_copy(update={"status": "confirmed", "proof_hash": ""}))
        # Failed attempt counts must commit before the safe error is raised.
        if failed:
            raise MentorError("identity_link_code_invalid")
        return {"status": "connected"}

    def cancel(self, identifier: str, owner_id: str) -> dict:
        with self.store.transaction() as db:
            link = self._owned(db, identifier, owner_id)
            if link.status == "confirmed":
                raise MentorError("identity_link_already_confirmed")
            self._save(db, link.model_copy(update={"status": "cancelled", "proof_hash": ""}))
        return {"status": "cancelled"}

    def _save(self, db: sqlite3.Connection, link: IdentityLink) -> None:
        self.store.put(db, "identity_link", link, link.owner_id)
