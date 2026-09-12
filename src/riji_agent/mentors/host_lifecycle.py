"""Authenticated, source-free invalidation of the original host's managed room."""
from __future__ import annotations

import hashlib
import threading

from riji_agent.mentors.models import Conversation, MentorError, Record
from riji_agent.mentors.store import key

EVENTS = frozenset({"im.chat.member.user.added_v1", "im.chat.member.user.deleted_v1",
    "im.chat.member.user.withdrawn_v1", "im.chat.updated_v1", "im.chat.member.bot.deleted_v1"})


class LifecycleReceipt(Record):
    id: str
    fingerprint: str
    received_at: float


class HostLifecycle:
    def __init__(self, bridge):
        self.bridge, self.store, self.now = bridge, bridge.store, bridge.now
        self.lock = threading.Lock()

    def receive(self, raw: dict) -> dict:
        host = self.bridge.host
        try:
            header, event = raw["header"], raw["event"]
            timestamp = header["create_time"]
            identifier, room = header["event_id"], event["chat_id"]
            if (raw.get("schema") != "2.0" or header.get("app_id") != host.external_id
                    or header.get("tenant_key") != host.tenant or header.get("event_type") not in EVENTS
                    or not isinstance(identifier, str) or not 1 <= len(identifier) <= 300
                    or not isinstance(room, str) or not 1 <= len(room) <= 300
                    or not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdigit()
                    or len(timestamp) > 16 or int(timestamp) <= 0 or not self.now() - 86400 <= int(timestamp) / 1000 <= self.now() + 300):
                raise ValueError
        except Exception:
            raise MentorError("host_lifecycle_invalid") from None
        receipt_id = hashlib.sha256(key(host.id, identifier).encode()).hexdigest()
        fingerprint = hashlib.sha256(key(header["event_type"], room, timestamp).encode()).hexdigest()
        with self.lock:
            with self.store.transaction() as db:
                previous = self.store.get(db, "host_lifecycle", receipt_id, LifecycleReceipt)
                if previous:
                    if previous.fingerprint != fingerprint:
                        raise MentorError("host_lifecycle_conflict")
                    return {"status": "accepted", "duplicate": True, "delivery": "none", "hermes_reply": False}
                records = self.store.records(db, "host_lifecycle", "", LifecycleReceipt)
                for item in records:
                    if item.received_at < self.now() - 86400:
                        db.execute("DELETE FROM mentor_records WHERE kind='host_lifecycle' AND id=?", (item.id,))
                can_record = sum(item.received_at >= self.now() - 86400 for item in records) < 2000
                target = self.store.lookup(db, "room", key(host.platform, host.tenant, room))
                conversation = self.store.get(db, "conversation", target, Conversation) if target else None
            if (conversation is None or conversation.source_scope != "group_only"
                    or conversation.source_application_id != host.id or conversation.status == "deleted"):
                raise MentorError("unmanaged_group")
            self.bridge.runtime.service.policy.block(conversation.id, "verification_paused", preserve_inactive=True)
            if can_record:
                with self.store.transaction() as db:
                    self.store.put(db, "host_lifecycle", LifecycleReceipt(id=receipt_id,
                        fingerprint=fingerprint, received_at=self.now()))
        return {"status": "accepted", "code": "group_verification_paused", "duplicate": False,
                "receipt_recorded": can_record,
                "delivery": "none", "hermes_reply": False}
