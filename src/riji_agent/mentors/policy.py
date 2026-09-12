"""Revalidate complete source dependencies and current audiences at every boundary."""

from __future__ import annotations

import hashlib
import time
from typing import Callable

from riji_agent.mentors.models import AudienceGrant, Conversation, Execution, MentorError, Principal, Source
from riji_agent.mentors.ports import ChannelPort, SourcePort
from riji_agent.mentors.store import MentorStore, key


class DiscussionPolicy:
    def __init__(self, store: MentorStore, sources: SourcePort, channel: ChannelPort,
                 now: Callable[[], float] = time.time) -> None:
        self.store, self.sources, self.channel, self.now = store, sources, channel, now

    def freeze(self, conversation: Conversation) -> Conversation:
        if conversation.source_scope == "group_only":
            self.group_content(conversation)
            return conversation
        principal = self.store.read("principal", conversation.owner_id, Principal)
        selected = self.sources.background(principal, conversation)
        permitted = tuple(source for source in selected if self._usable(source, principal, conversation))
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", conversation.id, Conversation)
            if current.input_revision != conversation.input_revision:
                raise MentorError("input_changed")
            for source in permitted:
                snapshot_id = key(principal.id, source.id, source.version)
                existing = self.store.get(db, "source", snapshot_id, Source)
                if existing is not None and existing != source:
                    raise MentorError("source_version_conflict")
                self.store.put(db, "source", source, principal.id, snapshot_id)
            current = current.model_copy(update={"source_ids": tuple(key(principal.id, source.id, source.version) for source in permitted)})
            self.store.put(db, "conversation", current, principal.id)
            return current

    def _usable(self, source: Source, principal: Principal, conversation: Conversation) -> bool:
        return (source.owner_id == principal.id
                and set(conversation.personas).issubset(source.allowed_personas)
                and self.sources.validate(principal, source))

    def background(self, conversation: Conversation) -> tuple[Source, ...]:
        if conversation.source_scope == "group_only":
            self.group_content(conversation)
            return ()
        items = tuple(self.store.read("source", identifier, Source) for identifier in conversation.source_ids)
        if any(item is None or item.owner_id != conversation.owner_id for item in items):
            raise MentorError("source_unavailable")
        return items

    def preview_hash(self, conversation: Conversation) -> str:
        sources = self.background(conversation)
        material = conversation.model_dump_json() + "".join(source.model_dump_json() for source in sources)
        return hashlib.sha256(material.encode()).hexdigest()

    def check(self, execution: Execution, *, require_lease: bool = True) -> Conversation:
        conversation = self.store.read("conversation", execution.conversation_id, Conversation)
        self._check_execution(conversation, execution, require_lease)
        principal = self.store.read("principal", conversation.owner_id, Principal)
        for source in self.background(conversation):
            if not self._usable(source, principal, conversation):
                self.block(conversation.id, "source_revoked", execution=execution)
                raise MentorError("source_revoked")
        if conversation.kind == "roundtable":
            self._check_audience(conversation, execution=execution)
        current = self.store.read("conversation", conversation.id, Conversation)
        self._check_execution(current, execution, require_lease)
        return current

    def _check_execution(self, conversation: Conversation | None, execution: Execution, require_lease: bool) -> None:
        if conversation is None or conversation.status in {"stopped", "deleted", "failed", "interrupted", "archived", "waiting_user"}:
            raise MentorError("execution_inactive")
        expected = (execution.run_id, execution.input_revision, execution.cancel_epoch)
        actual = (conversation.run_id, conversation.input_revision, conversation.cancel_epoch)
        if actual != expected:
            raise MentorError("execution_stale")
        if execution.owner_id and conversation.owner_id != execution.owner_id:
            raise MentorError("execution_stale")
        if require_lease and (conversation.lease_generation != execution.lease_generation
                              or conversation.lease_until <= self.now()):
            raise MentorError("lease_expired")

    @staticmethod
    def same_execution(conversation: Conversation, execution: Execution) -> bool:
        return ((not execution.owner_id or execution.owner_id == conversation.owner_id)
                and (conversation.id, conversation.run_id, conversation.input_revision,
                conversation.cancel_epoch, conversation.lease_generation) == (
                    execution.conversation_id, execution.run_id, execution.input_revision,
                    execution.cancel_epoch, execution.lease_generation))

    def _check_audience(self, conversation: Conversation, *, execution: Execution | None = None) -> None:
        grant = self.store.read("grant", conversation.grant_id, AudienceGrant)
        if (grant is None or not grant.active or grant.owner_id != conversation.owner_id
                or grant.input_revision != conversation.input_revision
                or conversation.room_status not in {"ready", "ended"}):
            raise MentorError("audience_not_authorized")
        try:
            current = (self.group_snapshot(conversation) if conversation.source_scope == "group_only"
                       else self.channel.inspect_room(conversation.room_id))
        except Exception as exc:
            self.block(conversation.id, "verification_paused", execution=execution)
            raise MentorError("audience_verification_failed") from None
        if not current.complete:
            self.block(conversation.id, "verification_paused", execution=execution)
            raise MentorError("audience_verification_failed")
        if current != grant.snapshot:
            self.block(conversation.id, "room_sealed", execution=execution)
            raise MentorError("audience_changed")
        versions = {source.id: source.version for source in self.background(conversation)}
        if versions != grant.source_versions:
            self.block(conversation.id, "source_revoked", execution=execution)
            raise MentorError("source_revoked")

    def group_snapshot(self, conversation: Conversation):
        from riji_agent.mentors.group_scope import current_snapshot
        return current_snapshot(self, conversation)

    def group_content(self, conversation: Conversation) -> None:
        from riji_agent.mentors.group_scope import check_content
        check_content(self.store, conversation)

    def block(self, conversation_id: str, reason: str, *, preserve_inactive: bool = False,
              execution: Execution | None = None) -> None:
        from riji_agent.mentors.budget import BudgetService
        with self.store.transaction() as db:
            conversation = self.store.get(db, "conversation", conversation_id, Conversation)
            if conversation is None or conversation.status == "deleted":
                return
            if execution is not None and not self.same_execution(conversation, execution):
                return
            BudgetService(self.store, self.now).pause(db, conversation)
            status = conversation.status if preserve_inactive and conversation.status in {"stopped", "archived"} else "interrupted"
            changes = {"status": status, "cancel_epoch": conversation.cancel_epoch + 1,
                       "lease_until": 0, "state_revision": conversation.state_revision + 1,
                       "updated_at": self.now()}
            if reason in {"room_sealed", "verification_paused"}:
                changes["room_status"] = "sealed" if reason == "room_sealed" else "verification_paused"
            self.store.put(db, "conversation", conversation.model_copy(update=changes), conversation.owner_id)
            self.store.bind(db, "blocked", conversation_id, reason)
            self.store.bind(db, "private_notice", conversation_id, reason)
            self._cancel_pending(db, conversation_id)

    def _cancel_pending(self, db, conversation_id: str) -> None:
        from riji_agent.mentors.models import Delivery
        for delivery in self.store.records(db, "delivery", conversation_id, Delivery):
            if delivery.status in {"pending", "sending"}:
                self.store.put(db, "delivery", delivery.model_copy(update={"status": "cancelled"}), conversation_id)
