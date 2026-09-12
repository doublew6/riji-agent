"""The sole command and lifecycle owner for private mentor discussions."""

from __future__ import annotations

import hashlib
import time
from typing import Callable

from riji_agent.mentors.budget import BudgetService
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.models import (
    Application, Artifact, AudienceGrant, ChatBinding, Command, Conversation,
    Delivery, MentorError, Principal, Receipt, new_id,
)
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.store import MentorStore, key


class DiscussionService:
    def __init__(self, store: MentorStore, identity: IdentityService, policy: DiscussionPolicy,
                 now: Callable[[], float] = time.time) -> None:
        self.store, self.identity, self.policy, self.now = store, identity, policy, now
        self.budgets = BudgetService(store, now)
        from riji_agent.mentors.summary import ProblemSummaries
        from riji_agent.mentors.lifecycle import ProblemLifecycle
        self.summaries = ProblemSummaries(self)
        self.lifecycle = ProblemLifecycle(self)

    def create(self, binding: ChatBinding, question: str, *, personas: tuple[str, ...],
               mode: str = "private", rounds: int = 2, run_personas: tuple[str, ...] = ()) -> Conversation:
        if binding.chat_type != "p2p":
            raise MentorError("private_preparation_required")
        app = self.store.read("application", binding.application_id, Application)
        if app is None or rounds not in {1, 2}:
            raise MentorError("invalid_discussion")
        for persona in personas:
            self.identity.personas.get(persona)
        private = mode == "private"
        if private and (len(personas) != 1 or (app.role == "mentor" and app.persona_id != personas[0])):
            raise MentorError("fixed_persona_required")
        if not private and (app.role != "host" or mode not in {"reference", "debate"}
                            or not 2 <= len(set(personas)) == len(personas) <= 4):
            raise MentorError("invalid_roundtable")
        if not private and app.platform == "feishu":
            # A linked principal may originate on the web. Never accidentally
            # provision a local room by routing on that enrollment platform.
            raise MentorError("feishu_roundtable_capability_not_verified")
        selected = run_personas or personas
        if not private and (not 2 <= len(set(selected)) == len(selected) <= 4
                            or not set(selected).issubset(personas)):
            raise MentorError("invalid_roundtable")
        conversation = Conversation(owner_id=binding.principal_id, question=question,
                                    personas=personas, mode=mode, rounds=rounds,
                                    run_personas=selected,
                                    kind="private" if private else "roundtable",
                                    status="queued" if private else "awaiting_share",
                                    room_status="ready" if private else "preparing",
                                    debate_started=mode == "debate", created_at=self.now(), updated_at=self.now())
        with self.store.transaction() as db:
            self.store.put(db, "conversation", conversation, conversation.owner_id)
            self._user_artifact(db, conversation, question)
            conversation = self.summaries.refresh(db, conversation)
            self.store.put(db, "conversation", conversation, conversation.owner_id)
            self.summaries.record_run(db, conversation)
            self.store.bind(db, "origin", conversation.id, binding.id)
            self.budgets.create(db, conversation)
            if private:
                self.budgets.activate(db, conversation)
        conversation = self.policy.freeze(conversation)
        if private:
            self.identity.select_conversation(binding, personas[0], conversation.id)
        return conversation

    def get(self, conversation_id: str, principal_id: str) -> Conversation:
        conversation = self.store.read("conversation", conversation_id, Conversation)
        if conversation is None or conversation.owner_id != principal_id or conversation.status == "deleted":
            raise MentorError("discussion_not_found")
        if conversation.kind == "roundtable" and not conversation.summary_id:
            with self.store.transaction() as db:
                current = self.store.get(db, "conversation", conversation.id, Conversation)
                if current.status == "deleted":
                    raise MentorError("discussion_not_found")
                conversation = self.summaries.refresh(db, current)
                self.store.put(db, "conversation", conversation, principal_id)
                self.summaries.record_run(db, conversation)
        return conversation

    def apply(self, command: Command, *, group_binding: ChatBinding | None = None) -> Receipt:
        fingerprint = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        with self.store.transaction() as db:
            previous = db.execute("SELECT * FROM mentor_commands WHERE id=?", (command.id,)).fetchone()
            if previous:
                if previous["owner"] != command.principal_id or previous["fingerprint"] != fingerprint:
                    raise MentorError("command_conflict")
                return Receipt.model_validate_json(previous["receipt"]).model_copy(update={"deduplicated": True})
            conversation = self.store.get(db, "conversation", command.conversation_id, Conversation)
            if conversation is None or conversation.owner_id != command.principal_id:
                raise MentorError("discussion_not_found")
            self.check_input_scope(conversation, command.kind, group_binding, db=db)
            if command.kind not in {"stop", "delete"} and command.expected_revision != conversation.input_revision:
                raise MentorError("input_changed")
            updated = self._apply(db, conversation, command)
            updated = updated.model_copy(update={"state_revision": conversation.state_revision + 1, "updated_at": self.now()})
            self.store.put(db, "conversation", updated, updated.owner_id)
            if updated.status != "deleted":
                self.summaries.record_run(db, updated)
            receipt = Receipt(command_id=command.id, conversation_id=updated.id,
                              status=updated.status, input_revision=updated.input_revision)
            db.execute("INSERT INTO mentor_commands VALUES (?,?,?,?)",
                       (command.id, command.principal_id, fingerprint, receipt.model_dump_json()))
        return receipt

    def check_input_scope(self, conversation: Conversation, kind: str,
                          binding: ChatBinding | None = None, *, db=None) -> None:
        if conversation.source_scope != "group_only" or kind in {"stop", "delete", "archive"}:
            return
        if (binding is None or binding.chat_type != "group" or binding.principal_id != conversation.owner_id
                or binding.application_id != conversation.source_application_id
                or binding.external_chat_id != conversation.room_id):
            raise MentorError("group_only_input_required")
        app = (self.store.get(db, "application", binding.application_id, Application) if db is not None
               else self.store.read("application", binding.application_id, Application))
        if (app is None or app.role != "host" or app.platform != conversation.source_platform
                or app.tenant != conversation.source_tenant):
            raise MentorError("group_only_input_required")

    def adopt_group(self, binding: ChatBinding, question: str, snapshot, *, replace_conversation_id: str = "") -> Conversation:
        """Called only by the authenticated host bridge after real current-room proof."""
        app = self.store.read("application", binding.application_id, Application)
        if (app is None or app.platform != "feishu" or app.role != "host" or binding.chat_type != "group"
                or snapshot.room_id != binding.external_chat_id):
            raise MentorError("group_adoption_invalid")
        from riji_agent.mentors.group_scope import GROUP_PERSONAS
        conversation = Conversation(owner_id=binding.principal_id, question=question,
            kind="roundtable", personas=GROUP_PERSONAS, run_personas=GROUP_PERSONAS, mode="reference",
            source_scope="group_only", source_application_id=app.id,
            source_platform=app.platform, source_tenant=app.tenant, room_id=binding.external_chat_id,
            status="completed", room_status="ready", run_kind="followup", run_number=0,
            created_at=self.now(), updated_at=self.now())
        verified = self.policy.group_snapshot(conversation)
        if snapshot != verified:
            raise MentorError("group_adoption_changed")
        grant = AudienceGrant(conversation_id=conversation.id, owner_id=conversation.owner_id,
            input_revision=1, snapshot=verified, source_versions={})
        conversation = conversation.model_copy(update={"grant_id": grant.id})
        with self.store.transaction() as db:
            room_key = key(app.platform, app.tenant, conversation.room_id)
            existing = self.store.lookup(db, "room", room_key)
            if existing:
                old = self.store.get(db, "conversation", existing, Conversation)
                if (existing != replace_conversation_id or old is None or old.owner_id != conversation.owner_id
                        or old.source_scope != "personal" or old.status != "archived" or old.room_status == "sealed"):
                    raise MentorError("room_already_bound")
                grant_old = self.store.get(db, "grant", old.grant_id, AudienceGrant)
                if grant_old is not None and grant_old.active:
                    raise MentorError("group_replacement_changed")
            elif replace_conversation_id:
                raise MentorError("group_replacement_changed")
            self.store.put(db, "conversation", conversation, conversation.owner_id)
            self.store.put(db, "grant", grant, conversation.owner_id)
            self.store.bind(db, "origin", conversation.id, binding.id)
            self.store.bind(db, "room", room_key, conversation.id)
            self.budgets.create(db, conversation)
        # The first actual ingress command records its user artifact once.
        return conversation

    def _apply(self, db, conversation: Conversation, command: Command) -> Conversation:
        if self.store.lookup(db, "archive_only", conversation.id) and command.kind not in {"delete", "stop"}:
            raise MentorError("restored_history_is_read_only")
        if conversation.status == "deleted":
            if command.kind == "delete":
                return conversation
            raise MentorError("discussion_not_found")
        if command.kind in {"stop", "delete"}:
            return self._stop(db, conversation, delete=command.kind == "delete")
        if command.kind in {"start_run", "reanalyze", "continue", "correct", "archive", "restore"}:
            return self.lifecycle.apply(db, conversation, command)
        if conversation.status == "archived":
            raise MentorError("problem_archived")
        if command.kind == "share":
            return self._share(db, conversation, command)
        if command.kind == "debate":
            return self._debate(db, conversation)
        if command.kind == "resume":
            return self._resume(db, conversation)
        if command.kind == "summarize":
            if conversation.status not in {"queued", "running", "waiting_user"}:
                raise MentorError("discussion_not_running")
            return conversation.model_copy(update={"summarize_requested": True})
        if command.kind in {"supplement", "followup"}:
            return self._supplement(db, conversation, command)
        raise MentorError("unknown_command")

    def _stop(self, db, conversation: Conversation, *, delete: bool) -> Conversation:
        self.budgets.pause(db, conversation)
        self.policy._cancel_pending(db, conversation.id)
        suspended = self.store.get(db, "suspended_run", conversation.id, Conversation)
        if suspended is not None:
            self.summaries.record_run(db, suspended.model_copy(update={"status": "stopped"}))
        db.execute("DELETE FROM mentor_records WHERE kind='suspended_run' AND id=?", (conversation.id,))
        if delete:
            db.execute("INSERT OR IGNORE INTO mentor_deleted VALUES (?,?)", (conversation.id, self.now()))
            db.execute("DELETE FROM mentor_records WHERE owner=? AND kind IN ('artifact','delivery','handoff','transfer','run','summary')", (conversation.id,))
            db.execute("DELETE FROM mentor_keys WHERE kind='superseded' AND value=?", (conversation.id,))
            db.execute("DELETE FROM mentor_steps WHERE conversation_id=?", (conversation.id,))
            if conversation.grant_id:
                db.execute("DELETE FROM mentor_records WHERE kind='grant' AND id=?", (conversation.grant_id,))
            db.execute("DELETE FROM mentor_records WHERE kind='local_room' AND id=?", (conversation.room_id,))
            runs = db.execute("SELECT id FROM mentor_budgets WHERE conversation_id=?", (conversation.id,)).fetchall()
            for run in runs:
                db.execute("DELETE FROM mentor_keys WHERE kind='followup_question' AND key=?", (run[0],))
            db.execute("DELETE FROM mentor_keys WHERE key=? AND kind NOT IN ('room','origin')", (conversation.id,))
            others = self.store.records(db, "conversation", conversation.owner_id, Conversation)
            retained = {source for item in others if item.id != conversation.id and item.status != "deleted" for source in item.source_ids}
            from riji_agent.mentors.models import Source
            for _ in range(9):
                inherited = set()
                for identifier in retained:
                    item = self.store.get(db, "source", identifier, Source)
                    if item:
                        inherited.update(item.dependencies)
                if inherited.issubset(retained):
                    break
                retained.update(inherited)
            for source in set(conversation.source_ids) - retained:
                db.execute("DELETE FROM mentor_records WHERE kind='source' AND id=?", (source,))
        return conversation.model_copy(update={
            "status": "deleted" if delete else "stopped", "cancel_epoch": conversation.cancel_epoch + 1,
            "lease_until": 0, "question": "[deleted]" if delete else conversation.question,
            "source_ids": () if delete else conversation.source_ids,
            "room_status": "sealed" if delete else conversation.room_status,
            "summary_id": "" if delete else conversation.summary_id,
            "summary_version": 0 if delete else conversation.summary_version,
            "suspended_run_id": "",
        })

    def _share(self, db, conversation: Conversation, command: Command) -> Conversation:
        if conversation.kind != "roundtable" or conversation.status != "awaiting_share":
            raise MentorError("share_not_pending")
        # The preview fingerprint is saved before this transaction is entered.
        expected = self.store.lookup(db, "share_preview", conversation.id)
        if not command.preview_hash or command.preview_hash != expected:
            raise MentorError("share_preview_required")
        self.store.bind(db, "room_operation", conversation.id, new_id())
        return conversation.model_copy(update={"status": "provisioning", "room_status": "creating"})

    def share_preview(self, conversation_id: str, principal_id: str) -> dict:
        conversation = self.get(conversation_id, principal_id)
        if conversation.status != "awaiting_share":
            raise MentorError("share_not_pending")
        fingerprint = self.policy.preview_hash(conversation)
        with self.store.transaction() as db:
            self.store.bind(db, "share_preview", conversation.id, fingerprint)
        return {"conversation_id": conversation.id, "input_revision": conversation.input_revision,
                "preview_hash": fingerprint, "question": conversation.question,
                "personas": conversation.personas, "run_personas": conversation.run_personas,
                "summary_version": conversation.summary_version,
                "sources": [source.model_dump() for source in self.policy.background(conversation)]}

    def _debate(self, db, conversation: Conversation) -> Conversation:
        if (conversation.mode != "reference" or conversation.debate_started
                or conversation.status != "completed" or conversation.run_kind == "followup"):
            raise MentorError("debate_not_available")
        self.budgets.activate(db, conversation)
        return conversation.model_copy(update={"mode": "debate", "debate_started": True,
                                                "status": "queued", "room_status": "ready"})

    def _resume(self, db, conversation: Conversation) -> Conversation:
        if conversation.suspended_run_id:
            return self.lifecycle.resume(db, conversation)
        if conversation.status not in {"interrupted", "waiting_user", "stopped"}:
            raise MentorError("resume_not_available")
        if conversation.room_status in {"sealed", "creating", "creation_unknown", "verification_paused"}:
            raise MentorError("room_reverification_required")
        if conversation.kind == "roundtable":
            conversation = self.summaries.prepare(db, conversation)
        if db.execute("SELECT 1 FROM mentor_steps WHERE conversation_id=? AND status IN ('unknown','call_sent')", (conversation.id,)).fetchone():
            raise MentorError("step_reconciliation_required")
        deliveries = self.store.records(db, "delivery", conversation.id, Delivery)
        if any(item.status in {"unknown", "sending", "failed"} for item in deliveries):
            raise MentorError("delivery_reconciliation_required")
        db.execute("DELETE FROM mentor_steps WHERE conversation_id=? AND status='failed'", (conversation.id,))
        self.budgets.activate(db, conversation)
        return conversation.model_copy(update={"status": "queued", "lease_until": 0})

    def _supplement(self, db, conversation: Conversation, command: Command) -> Conversation:
        if conversation.kind == "roundtable":
            return self.lifecycle.supplement(db, conversation, command)
        if not command.text.strip():
            raise MentorError("question_required")
        completed = conversation.status in {"completed", "partial", "stopped"}
        if command.kind == "followup" and not completed:
            raise MentorError("discussion_still_running")
        if conversation.room_status in {"sealed", "verification_paused", "creation_unknown", "creating"}:
            raise MentorError("room_reverification_required")
        if completed:
            actor = command.actor or (conversation.personas[0] if conversation.kind == "private" else "host")
            if actor not in (*conversation.personas, "host"):
                raise MentorError("actor_not_allowed")
            updated = conversation.model_copy(update={"run_id": new_id(), "run_kind": "followup",
                                                      "followup_actor": actor, "status": "queued", "lease_until": 0})
            self.store.bind(db, "followup_question", updated.run_id, command.text)
            self._user_artifact(db, updated, command.text)
            self.budgets.activate(db, updated)
            return updated
        if conversation.status not in {"queued", "running", "waiting_user"}:
            raise MentorError("supplement_not_available")
        if len(conversation.question) + len(command.text) + 4 > 10000:
            raise MentorError("question_too_long")
        self.policy._cancel_pending(db, conversation.id)
        updated = conversation.model_copy(update={"question": conversation.question + "\n补充：" + command.text,
                                                  "input_revision": conversation.input_revision + 1,
                                                  "cancel_epoch": conversation.cancel_epoch + 1,
                                                  "lease_until": 0, "status": "queued"})
        if conversation.grant_id:
            grant = self.store.get(db, "grant", conversation.grant_id, AudienceGrant)
            self.store.put(db, "grant", grant.model_copy(update={"input_revision": updated.input_revision}), conversation.owner_id)
        self._user_artifact(db, updated, command.text)
        return updated

    def _user_artifact(self, db, conversation: Conversation, text: str, *, statement_kind: str = "user_statement") -> None:
        artifact = Artifact(conversation_id=conversation.id, actor="user", kind="user",
                            input_revision=conversation.input_revision, run_id=conversation.run_id,
                            origin_kind=statement_kind, text=text, created_at=self.now(),
                            origin_room_id=conversation.room_id if conversation.source_scope == "group_only" else "")
        self.store.put(db, "artifact", artifact, conversation.id)

    def applications(self, conversation: Conversation) -> tuple[Application, ...]:
        with self.store.transaction() as db:
            origin_id = self.store.lookup(db, "origin", conversation.id)
            binding = self.store.get(db, "chat", origin_id, ChatBinding) if origin_id else None
            origin = self.store.get(db, "application", binding.application_id, Application) if binding else None
        if origin is None or binding.principal_id != conversation.owner_id:
            raise MentorError("application_configuration_required")
        if origin.platform == "feishu" and conversation.source_scope != "group_only":
            raise MentorError("feishu_roundtable_capability_not_verified")
        if conversation.source_scope == "group_only":
            self.check_input_scope(conversation, "start_run", binding)
        apps = self.store.list("application", "", Application)
        wanted = ("host", *conversation.personas)
        selected = []
        for actor in wanted:
            matches = [app for app in apps if app.persona_id == actor and app.platform == origin.platform
                       and app.tenant == origin.tenant]
            if len(matches) != 1:
                raise MentorError("application_configuration_required")
            selected.append(matches[0])
        return tuple(selected)

    def provision(self, conversation_id: str) -> Conversation:
        conversation = self.store.read("conversation", conversation_id, Conversation)
        if conversation is None or conversation.status != "provisioning":
            raise MentorError("provision_not_pending")
        applications = self.applications(conversation)
        principal = self.store.read("principal", conversation.owner_id, Principal)
        with self.store.transaction() as db:
            operation = self.store.lookup(db, "room_operation", conversation.id)
            if self.store.lookup(db, "room_attempt", conversation.id):
                raise MentorError("room_reconciliation_required")
            self.store.bind(db, "room_attempt", conversation.id, operation)
        try:
            room_id = self.policy.channel.create_room(principal, tuple(app.id for app in applications), operation)
        except MentorError as exc:
            self.policy.block(conversation.id, exc.code)
            raise
        except Exception:
            self._provision_unknown(conversation.id)
            raise MentorError("room_creation_unknown") from None
        return self.verify_room(conversation.id, room_id)

    def verify_room(self, conversation_id: str, room_id: str) -> Conversation:
        conversation = self.store.read("conversation", conversation_id, Conversation)
        if (conversation is None or conversation.room_status not in {"creating", "creation_unknown", "verification_paused"}
                or (conversation.room_id and conversation.room_id != room_id)):
            raise MentorError("room_not_available")
        principal = self.store.read("principal", conversation.owner_id, Principal)
        applications = tuple(app.id for app in self.applications(conversation))
        snapshot = self.policy.channel.inspect_room(room_id)
        previous = self.store.read("grant", conversation.grant_id, AudienceGrant) if conversation.grant_id else None
        if previous and snapshot.complete and previous.snapshot != snapshot:
            self.policy.block(conversation.id, "room_sealed")
            raise MentorError("audience_changed")
        valid = (snapshot.room_id == room_id and snapshot.complete and snapshot.private
                 and snapshot.management_restricted and snapshot.history_restricted and snapshot.continuity_verified
                 and set(snapshot.human_subjects) == {principal.account.subject}
                 and set(snapshot.application_ids) == set(applications))
        if not valid:
            self._remember_room(conversation, room_id)
            self.policy.block(conversation.id, "verification_paused")
            raise MentorError("room_not_verified")
        grant = AudienceGrant(conversation_id=conversation.id, owner_id=principal.id,
                              input_revision=conversation.input_revision, snapshot=snapshot,
                              source_versions={source.id: source.version for source in self.policy.background(conversation)})
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", conversation.id, Conversation)
            if current.input_revision != conversation.input_revision or current.status in {"deleted", "stopped"}:
                raise MentorError("input_changed")
            room_key = key(principal.account.platform, principal.account.tenant, room_id)
            existing = self.store.lookup(db, "room", room_key)
            if existing and existing != current.id:
                raise MentorError("room_already_bound")
            current = current.model_copy(update={"room_id": room_id, "room_status": "ready", "grant_id": grant.id,
                                                  "status": "interrupted" if previous else "queued", "updated_at": self.now()})
            self.store.put(db, "grant", grant, principal.id)
            self.store.put(db, "conversation", current, principal.id)
            self.store.bind(db, "room", key(principal.account.platform, principal.account.tenant, room_id), current.id)
            self.budgets.activate(db, current)
            return current

    def _remember_room(self, conversation: Conversation, room_id: str) -> None:
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", conversation.id, Conversation)
            self.store.put(db, "conversation", current.model_copy(update={"room_id": room_id}), current.owner_id)

    def _provision_unknown(self, conversation_id: str) -> None:
        with self.store.transaction() as db:
            conversation = self.store.get(db, "conversation", conversation_id, Conversation)
            self.store.put(db, "conversation", conversation.model_copy(update={"status": "interrupted", "room_status": "creation_unknown"}), conversation.owner_id)

    def recover(self) -> int:
        with self.store.transaction() as db:
            rows = db.execute("SELECT value FROM mentor_records WHERE kind='conversation'").fetchall()
            count = 0
            for row in rows:
                conversation = Conversation.model_validate_json(row[0])
                if conversation.kind == "roundtable" and not conversation.summary_id and conversation.status != "deleted":
                    conversation = self.summaries.refresh(db, conversation)
                    self.store.put(db, "conversation", conversation, conversation.owner_id)
                    self.summaries.record_run(db, conversation)
                if conversation.status not in {"running", "queued", "provisioning", "delivering"}:
                    continue
                self.budgets.pause(db, conversation)
                updated = conversation.model_copy(update={"status": "interrupted", "lease_until": 0,
                                                          "cancel_epoch": conversation.cancel_epoch + 1})
                self.store.put(db, "conversation", updated, updated.owner_id)
                for delivery in self.store.records(db, "delivery", updated.id, Delivery):
                    if delivery.status == "sending":
                        self.store.put(db, "delivery", delivery.model_copy(update={"status": "unknown"}), updated.id)
                self.policy._cancel_pending(db, updated.id)
                count += 1
            return count
