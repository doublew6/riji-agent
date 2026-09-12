"""Owner requests a room; only its next verified real message supplies content."""
from __future__ import annotations

from typing import Literal

from riji_agent.mentors.group_dialogue import parse_group_intent
from riji_agent.mentors.group_scope import GROUP_PERSONAS
from riji_agent.mentors.models import AudienceGrant, Conversation, Delivery, MentorError, Record, RoomSnapshot
from riji_agent.mentors.store import key


class GroupAdoption(Record):
    id: str
    owner_id: str
    application_id: str
    room_id: str
    snapshot: RoomSnapshot
    created_at: float
    expires_at: float
    status: Literal["awaiting_message", "adopted"] = "awaiting_message"
    conversation_id: str = ""
    replace_conversation_id: str = ""


class GroupAdoptions:
    def __init__(self, bridge):
        self.bridge, self.store = bridge, bridge.store
        self.service, self.now = bridge.runtime.service, bridge.now

    def _probe(self, owner_id: str, room_id: str) -> Conversation:
        host = self.bridge.host
        return Conversation(owner_id=owner_id, kind="roundtable", personas=GROUP_PERSONAS,
            question="[Current group verification]", mode="reference", source_scope="group_only",
            source_application_id=host.id, source_platform=host.platform, source_tenant=host.tenant,
            room_id=room_id, created_at=self.now(), updated_at=self.now())

    def prepare(self, owner_id: str, room_id: str, replace_conversation_id: str = "") -> dict:
        with self.bridge._lock:
            return self._prepare(owner_id, room_id, replace_conversation_id)

    def _prepare(self, owner_id: str, room_id: str, replace_conversation_id: str) -> dict:
        probe = self._probe(owner_id, room_id)
        snapshot = self.service.policy.group_snapshot(probe)
        identifier = key(probe.source_platform, probe.source_tenant, room_id)
        with self.store.transaction() as db:
            existing = self.store.lookup(db, "room", identifier)
            if existing:
                if not replace_conversation_id or existing != replace_conversation_id:
                    raise MentorError("room_already_bound")
                self._archive_previous(db, owner_id, existing, room_id)
            elif replace_conversation_id:
                raise MentorError("group_replacement_changed")
            # At most one unconsumed request per owner. No user body is stored.
            db.execute("DELETE FROM mentor_records WHERE kind='group_adoption' AND owner=?", (owner_id,))
            record = GroupAdoption(id=identifier, owner_id=owner_id, application_id=self.bridge.host.id,
                room_id=room_id, snapshot=snapshot, created_at=self.now(), expires_at=self.now() + 600,
                replace_conversation_id=replace_conversation_id)
            self.store.put(db, "group_adoption", record, owner_id)
        return {"archived_conversation_id": replace_conversation_id, "status": "awaiting_group_message", "source_scope": "group_only", "expires_at": record.expires_at,
                "personal_sources_enabled": False, "history_restricted": snapshot.history_restricted,
                "continuity_verified": snapshot.continuity_verified}

    def _archive_previous(self, db, owner_id: str, identifier: str, room_id: str) -> None:
        previous = self.store.get(db, "conversation", identifier, Conversation)
        if (previous is None or previous.owner_id != owner_id or previous.kind != "roundtable"
                or previous.source_scope != "personal" or previous.room_id != room_id
                or previous.status == "deleted" or previous.room_status == "sealed"):
            raise MentorError("group_replacement_not_allowed")
        deliveries = self.store.records(db, "delivery", identifier, Delivery)
        if any(item.status in {"sending", "unknown", "failed"} for item in deliveries):
            raise MentorError("delivery_reconciliation_required")
        stopped = self.service._stop(db, previous, delete=False).model_copy(update={
            "status": "archived", "state_revision": previous.state_revision + 1, "updated_at": self.now()})
        self.store.put(db, "conversation", stopped, owner_id)
        self.service.summaries.record_run(db, stopped)
        grant = self.store.get(db, "grant", stopped.grant_id, AudienceGrant)
        if grant is not None:
            self.store.put(db, "grant", grant.model_copy(update={"active": False}), owner_id)

    def pending(self, room_id: str, owner_id: str) -> bool:
        host = self.bridge.host
        record = self.store.read("group_adoption", key(host.platform, host.tenant, room_id), GroupAdoption)
        return bool(record and record.owner_id == owner_id and record.status == "awaiting_message")

    def consume(self, message, owner_id: str, created_at: float) -> Conversation:
        host = self.bridge.host
        identifier = key(host.platform, host.tenant, message.external_chat_id)
        record = self.store.read("group_adoption", identifier, GroupAdoption)
        if (record is None or record.owner_id != owner_id or record.application_id != host.id
                or record.status != "awaiting_message" or self.now() >= record.expires_at
                or created_at < record.created_at):
            raise MentorError("unmanaged_group")
        intent = parse_group_intent(message.text, message.mentioned_names)
        if intent.kind not in {"supplement", "followup", "start_run"} or not intent.text.strip():
            raise MentorError("group_topic_message_required")
        principal, app, binding = self.service.identity.resolve(host.id, message)
        if principal.id != owner_id or app != host:
            raise MentorError("group_owner_unverified")
        conversation = self.service.adopt_group(binding, intent.text, record.snapshot,
            replace_conversation_id=record.replace_conversation_id)
        with self.store.transaction() as db:
            self.store.put(db, "group_adoption", record.model_copy(update={
                "status": "adopted", "conversation_id": conversation.id}), owner_id)
        return conversation

    def revalidate(self, owner_id: str, identifier: str) -> dict:
        conversation = self.service.get(identifier, owner_id)
        if conversation.source_scope != "group_only" or conversation.status == "archived":
            raise MentorError("group_revalidation_unavailable")
        self.service.policy.group_content(conversation)
        try:
            snapshot = self.service.policy.group_snapshot(conversation)
        except Exception:
            self.service.policy.block(conversation.id, "verification_paused")
            raise MentorError("group_room_unverified") from None
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", conversation.id, Conversation)
            if current != conversation:
                raise MentorError("input_changed")
            grant = AudienceGrant(conversation_id=current.id, owner_id=owner_id,
                input_revision=current.input_revision, snapshot=snapshot, source_versions={})
            current = self.service._stop(db, current, delete=False).model_copy(update={
                "room_status": "ready", "grant_id": grant.id, "state_revision": current.state_revision + 1})
            self.store.put(db, "grant", grant, owner_id)
            self.store.put(db, "conversation", current, owner_id)
            db.execute("DELETE FROM mentor_keys WHERE kind IN ('blocked','private_notice') AND key=?", (current.id,))
        return {"conversation_id": current.id, "status": "stopped", "room_status": "ready",
                "source_scope": "group_only", "personal_sources_enabled": False}
