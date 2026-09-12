"""Bounded, provenance-preserving history for one private mentor conversation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from riji_agent.mentors.models import (
    Application, Artifact, ChatBinding, Conversation, Delivery, MentorError,
)

if TYPE_CHECKING:
    from riji_agent.mentors.service import DiscussionService

MAX_PRIVATE_HISTORY_ITEMS = 24
MAX_PRIVATE_HISTORY_CHARS = 18000


def private_history(service: DiscussionService, conversation: Conversation,
                    artifacts: tuple[Artifact, ...]) -> tuple[Artifact, ...]:
    """Select only this fixed private window's user text and delivered AI turns.

    Historical dependencies must remain in the conversation's frozen sources.
    DiscussionPolicy.check revalidates those sources before each model send and
    before delivery, including changes after this selection was assembled.
    """
    binding = _check_binding(service, conversation)
    deliveries = service.store.list("delivery", conversation.id, Delivery)
    sent = {item.artifact_id for item in deliveries
            if item.conversation_id == conversation.id and item.status == "sent"
            and item.application_id == binding.application_id and item.chat_id == binding.external_chat_id}
    selected = tuple(item for item in artifacts
                     if item.conversation_id == conversation.id
                     and item.input_revision <= conversation.input_revision
                     and ((item.kind == "user" and item.actor == "user")
                          or (item.kind == "followup" and item.actor == conversation.personas[0]
                              and item.id in sent)))
    with service.store.transaction() as db:
        superseded = service.summaries.superseded(db, conversation)
    selected = _bounded_history(tuple(item for item in selected if item.id not in superseded))
    if any(not set(item.dependencies).issubset(conversation.source_ids) for item in selected):
        raise MentorError("source_unavailable")
    return selected


def _check_binding(service: DiscussionService, conversation: Conversation) -> ChatBinding:
    if conversation.kind != "private" or len(conversation.personas) != 1:
        raise MentorError("fixed_persona_required")
    with service.store.transaction() as db:
        origin = service.store.lookup(db, "origin", conversation.id)
        binding = service.store.get(db, "chat", origin, ChatBinding) if origin else None
        app = service.store.get(db, "application", binding.application_id, Application) if binding else None
    if (binding is None or binding.principal_id != conversation.owner_id
            or binding.chat_type != "p2p" or app is None
            or (app.role != "host" and app.persona_id != conversation.personas[0])):
        raise MentorError("private_history_scope_invalid")
    return binding


def _bounded_history(artifacts: tuple[Artifact, ...]) -> tuple[Artifact, ...]:
    """Keep a contiguous suffix of whole artifacts, including JSON metadata."""
    selected: list[Artifact] = []
    characters = 2  # JSON list brackets.
    for item in reversed(artifacts):
        size = len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False))
        size += 2 if selected else 0  # Default json.dumps list separator.
        if len(selected) >= MAX_PRIVATE_HISTORY_ITEMS or characters + size > MAX_PRIVATE_HISTORY_CHARS:
            if not selected:
                raise MentorError("private_history_too_large")
            break
        selected.append(item)
        characters += size
    return tuple(reversed(selected))
