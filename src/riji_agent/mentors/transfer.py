"""Explicit, previewed excerpts across questions; dependencies retain their scope."""

from __future__ import annotations

from pydantic import Field

from riji_agent.mentors.history import digest
from riji_agent.mentors.models import Artifact, Conversation, MentorError, Principal, Record, Source, new_id
from riji_agent.mentors.store import key


class Transfer(Record):
    id: str = Field(default_factory=new_id)
    owner_id: str
    origin_id: str
    artifact_ids: tuple[str, ...]
    personas: tuple[str, ...]
    text: str
    fingerprint: str
    dependencies: tuple[str, ...] = ()
    accepted: bool = False
    target_id: str = ""
    content_kind: str = "unknown"


class TransferSources:
    def __init__(self, store, primary) -> None:
        self.store, self.primary = store, primary

    def background(self, principal, conversation):
        if conversation.source_scope == "group_only":
            if conversation.source_ids:
                raise MentorError("group_only_source_violation")
            return ()
        return self.primary.background(principal, conversation)

    def validate(self, principal: Principal, source: Source) -> bool:
        if source.kind != "shared_excerpt":
            return self.primary.validate(principal, source)
        with self.store.transaction() as db:
            return self.validate_in_transaction(db, principal, source)

    def validate_in_transaction(self, db, principal: Principal, source: Source) -> bool:
        """Revalidate local provenance while the caller owns the mentor transaction."""
        return self._validate(db, principal, source, 0)

    def _validate(self, db, principal: Principal, source: Source, depth: int) -> bool:
        if depth > 8 or source.owner_id != principal.id:
            return False
        if source.kind != "shared_excerpt":
            return self.primary.validate(principal, source)
        transfer = self.store.get(db, "transfer", source.id, Transfer)
        if (transfer is None or not transfer.accepted or transfer.owner_id != principal.id
                or transfer.fingerprint != source.version or transfer.text != source.text
                or transfer.origin_id != source.origin or transfer.personas != source.allowed_personas
                or transfer.content_kind != source.content_kind
                or not self._origin_current(db, transfer, principal.id)):
            return False
        dependencies = transfer.dependencies
        if dependencies != source.dependencies:
            return False
        for dependency in dependencies:
            parent = self.store.get(db, "source", dependency, Source)
            if (parent is None or not set(source.allowed_personas).issubset(parent.allowed_personas)
                    or not self._validate(db, principal, parent, depth + 1)):
                return False
        return True

    def _origin_current(self, db, transfer: Transfer, owner: str) -> bool:
        origin = self.store.get(db, "conversation", transfer.origin_id, Conversation)
        if origin is None or origin.owner_id != owner or origin.status == "deleted":
            return False
        items = [item for item in self.store.records(db, "artifact", transfer.origin_id, Artifact)
                 if item.id in transfer.artifact_ids]
        if len(items) != len(transfer.artifact_ids) or any(
                self.store.lookup(db, "superseded", item.id) for item in items):
            return False
        return transfer.text == "\n\n".join(item.actor + "：" + item.text for item in items)


class DiscussionTransfer:
    def __init__(self, history) -> None:
        self.history, self.service, self.store = history, history.service, history.store

    def preview(self, origin_id: str, owner: str, artifacts: tuple[str, ...], personas: tuple[str, ...]) -> dict:
        if not 1 <= len(personas) <= 4 or len(set(personas)) != len(personas):
            raise MentorError("invalid_transfer_personas")
        for actor in personas:
            self.service.identity.personas.get(actor)
        view = self.history.read(origin_id, owner)
        items = [item for item in view["artifacts"] if item["id"] in artifacts]
        if not 1 <= len(items) == len(artifacts) <= 4 or any("unavailable" in item or item.get("superseded") for item in items):
            raise MentorError("invalid_transfer_selection")
        dependencies = tuple(sorted({dep for item in items for dep in item["dependencies"]}))
        for dependency in dependencies:
            source = self.store.read("source", dependency, Source)
            if source is None or not set(personas).issubset(source.allowed_personas):
                raise MentorError("transfer_source_scope_restricted")
        text = "\n\n".join(item["actor"] + "：" + item["text"] for item in items)
        if len(text) > 4000:
            raise MentorError("transfer_excerpt_too_large")
        fingerprint = digest({"origin": origin_id, "items": items, "personas": personas})
        transfer = Transfer(owner_id=owner, origin_id=origin_id, artifact_ids=artifacts,
                            personas=personas, text=text, fingerprint=fingerprint, dependencies=dependencies,
                            content_kind=excerpt_content_kind(items))
        with self.store.transaction() as db:
            self.store.put(db, "transfer", transfer, origin_id)
        return transfer.model_dump()

    def accept(self, identifier: str, owner: str, fingerprint: str, target_id: str) -> dict:
        transfer = self.store.read("transfer", identifier, Transfer)
        target = self.service.get(target_id, owner)
        if target.source_scope == "group_only":
            raise MentorError("group_only_source_violation")
        if (transfer is None or transfer.owner_id != owner or transfer.fingerprint != fingerprint
                or transfer.accepted or target.status not in {"awaiting_share", "stopped"}
                or target.personas != transfer.personas or target.id == transfer.origin_id):
            raise MentorError("transfer_preview_changed")
        items = [self.store.read("artifact", identifier, Artifact) for identifier in transfer.artifact_ids]
        if any(item is None for item in items):
            raise MentorError("transfer_source_unavailable")
        dependencies = tuple(sorted({dep for item in items for dep in item.dependencies}))
        source = Source(id=transfer.id, owner_id=owner, version=fingerprint, text=transfer.text,
            kind="shared_excerpt", allowed_personas=transfer.personas, dependencies=dependencies,
            origin=transfer.origin_id, content_kind=transfer.content_kind)
        principal = self.store.read("principal", owner, Principal)
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", target.id, Conversation)
            origin = self.store.get(db, "conversation", transfer.origin_id, Conversation)
            pending = self.store.get(db, "transfer", transfer.id, Transfer)
            if (current != target or self.store.lookup(db, "archive_only", current.id)
                    or pending != transfer or origin is None or origin.status == "deleted"):
                raise MentorError("transfer_target_changed")
            if any(item.conversation_id != origin.id or self.store.lookup(db, "superseded", item.id)
                   for item in items):
                raise MentorError("transfer_source_unavailable")
            accepted = transfer.model_copy(update={"accepted": True, "target_id": target.id})
            self.store.put(db, "transfer", accepted, target.id)
            snapshot_id = key(owner, source.id, source.version)
            self.store.put(db, "source", source, owner, snapshot_id)
            updated = current.model_copy(update={"source_ids": (*current.source_ids, snapshot_id),
                "input_revision": current.input_revision + 1, "cancel_epoch": current.cancel_epoch + 1})
            self.store.put(db, "conversation", updated, owner)
            db.execute("DELETE FROM mentor_keys WHERE kind='share_preview' AND key=?", (target.id,))
        if not self.service.policy.sources.validate(principal, source):
            self.service.policy.block(target.id, "transfer_source_unavailable")
            raise MentorError("transfer_source_unavailable")
        return {"conversation_id": target.id, "status": updated.status, "input_revision": updated.input_revision}


def excerpt_content_kind(items: list[dict]) -> str:
    """Preserve source authorship without inferring facts from quoted text."""
    user = [item for item in items if item["kind"] == "user"]
    if not user:
        return "ai_discussion"
    if len(user) != len(items):
        return "mixed"
    kinds = {item.get("origin_kind", "user_statement") for item in user}
    if len(kinds) == 1 and kinds.issubset({"user_plan", "user_feedback"}):
        return next(iter(kinds))
    return "user_statement"
