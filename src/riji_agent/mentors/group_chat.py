"""Persistent member-only mobile chats, independent of legacy hosted roundtables."""

from __future__ import annotations

import sqlite3
import json
from typing import TYPE_CHECKING, TypeVar

from riji_agent.mentors.models import Artifact, Command, Conversation, Delivery, MentorError, SummaryItem, new_id
from riji_agent.mentors.store import MentorStore

if TYPE_CHECKING:
    from riji_agent.mentors.service import DiscussionService

ContextItem = TypeVar("ContextItem", Artifact, SummaryItem)


def apply_group_command(service: DiscussionService, db: sqlite3.Connection,
                        current: Conversation, command: Command) -> Conversation:
    if command.kind in {"stop", "delete"}:
        return service._stop(db, current, delete=command.kind == "delete")
    if command.kind == "end_turn":
        if current.status == "archived":
            raise MentorError("problem_archived")
        service.store.bind(db, "ended_group_turn", current.run_id, current.id)
        return advance_group_revision(service, db, service._stop(db, current, delete=False))
    if command.kind in {"archive", "restore"}:
        if command.kind == "archive":
            check_group_idle(service, db, current)
        updated = service.lifecycle.apply(db, current, command)
        return advance_group_revision(service, db, updated)
    if current.status == "archived":
        raise MentorError("problem_archived")
    if command.kind == "rename":
        check_group_idle(service, db, current)
        title = command.text.strip()
        if not title or len(title) > 120:
            raise MentorError("invalid_group_name")
        return advance_group_revision(service, db, current.model_copy(update={"title": title}))
    if command.kind == "resume" and current.run_number:
        if service.store.lookup(db, "ended_group_turn", current.run_id) == current.id:
            raise MentorError("group_turn_ended")
        updated = service._resume(db, current)
        restore_unsent_replies(service, db, updated)
        return updated
    if command.kind != "group_message":
        raise MentorError("group_chat_action_not_available")
    return start_group_reply(service, db, current, command)


def check_group_idle(service: DiscussionService, db: sqlite3.Connection, current: Conversation) -> None:
    pending = any(item.status == "pending" for item in service.store.records(db, "delivery", current.id, Delivery))
    if current.status not in {"completed", "partial", "stopped"} or pending:
        raise MentorError("discussion_still_running")
    service.lifecycle._reconciled(db, current)


def check_group_unresolved(service: DiscussionService, db: sqlite3.Connection, current: Conversation) -> None:
    ended = {row[0] for row in db.execute("SELECT key FROM mentor_keys WHERE kind='ended_group_turn' AND value=?", (current.id,))}
    rows = db.execute("SELECT key FROM mentor_steps WHERE conversation_id=? AND status IN ('unknown','call_sent')", (current.id,))
    for row in rows:
        try:
            run_id = json.loads(row[0])[0]
        except (ValueError, TypeError, IndexError):
            raise MentorError("step_reconciliation_required") from None
        if run_id not in ended:
            raise MentorError("step_reconciliation_required")
    for delivery in service.store.records(db, "delivery", current.id, Delivery):
        if delivery.status not in {"unknown", "sending", "failed"}:
            continue
        artifact = service.store.get(db, "artifact", delivery.artifact_id, Artifact)
        if artifact is None or artifact.run_id not in ended:
            raise MentorError("delivery_reconciliation_required")


def advance_group_revision(service: DiscussionService, db: sqlite3.Connection,
                           current: Conversation) -> Conversation:
    updated = current.model_copy(update={"input_revision": current.input_revision + 1})
    service.lifecycle._advance_grant(db, updated)
    return updated


def restore_unsent_replies(service: DiscussionService, db: sqlite3.Connection, current: Conversation) -> None:
    for delivery in service.store.records(db, "delivery", current.id, Delivery):
        if delivery.status != "cancelled" or delivery.attempts or delivery.first_attempt_at:
            continue
        artifact = service.store.get(db, "artifact", delivery.artifact_id, Artifact)
        if artifact is not None and artifact.run_id == current.run_id and artifact.actor in current.run_personas:
            restored = delivery.model_copy(update={"status": "pending", "cancel_epoch": current.cancel_epoch,
                                                  "input_revision": current.input_revision})
            service.store.put(db, "delivery", restored, current.id)


def start_group_reply(service: DiscussionService, db: sqlite3.Connection,
                      current: Conversation, command: Command) -> Conversation:
    if not command.text.strip():
        raise MentorError("question_required")
    if current.status not in {"completed", "partial", "stopped"}:
        raise MentorError("discussion_still_running")
    service.lifecycle._room(current)
    service.lifecycle._reconciled(db, current)
    actors = command.personas or current.personas
    if (len(set(actors)) != len(actors) or not set(actors).issubset(current.personas)
            or not 1 <= len(actors) <= 4):
        raise MentorError("actor_not_allowed")
    if current.run_number:
        service.summaries.record_run(db, current)
    updated = current.model_copy(update={
        "question": command.text, "run_id": new_id(), "run_number": current.run_number + 1,
        "run_kind": "group_chat", "run_personas": actors, "followup_actor": "",
        "input_revision": current.input_revision + 1, "cancel_epoch": current.cancel_epoch + 1,
        "status": "queued", "room_status": "ready", "lease_until": 0,
        "summarize_requested": False,
    })
    service._user_artifact(db, updated, command.text, statement_kind=command.statement_kind)
    updated = service.summaries.refresh(db, updated)
    service.lifecycle._advance_grant(db, updated)
    service.budgets.activate(db, updated)
    db.execute("DELETE FROM mentor_keys WHERE key=? AND kind IN ('blocked','private_notice')", (current.id,))
    return updated


def visible_group_artifacts(store: MentorStore, current: Conversation, artifacts: tuple[Artifact, ...], *,
                            db: sqlite3.Connection | None = None) -> tuple[Artifact, ...]:
    deliveries = (store.records(db, "delivery", current.id, Delivery) if db is not None
                  else store.list("delivery", current.id, Delivery))
    sent = {item.artifact_id for item in deliveries if item.status == "sent" and item.chat_id == current.room_id}
    return tuple(item for item in artifacts if item.conversation_id == current.id
                 and (item.kind == "user" or item.id in sent))


def recent_group_items(items: tuple[ContextItem, ...], *, max_items: int = 48,
                       max_chars: int = 18000) -> tuple[ContextItem, ...]:
    """Bound only model context; the complete conversation archive remains intact."""
    recent, size = [], 0
    for item in reversed(items):
        if len(recent) >= max_items or size + len(item.text) > max_chars:
            break
        recent.append(item)
        size += len(item.text)
    return tuple(reversed(recent))
