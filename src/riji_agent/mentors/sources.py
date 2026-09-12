"""Reuse authoritative memory records without importing another mentor's history."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from riji_agent.memory.models import MemoryScope, MemoryStatus
from riji_agent.mentors.models import Conversation, Principal, Source
from riji_agent.personas.registry import PersonaRegistry


class MemorySources:
    def __init__(self, service, personas: PersonaRegistry) -> None:
        self.service, self.personas = service, personas

    def background(self, principal: Principal, conversation: Conversation) -> tuple[Source, ...]:
        if conversation.source_scope == "group_only":
            if conversation.source_ids:
                from riji_agent.mentors.models import MentorError
                raise MentorError("group_only_source_violation")
            return ()
        if self.service is None:
            return ()
        contexts = [self.service.retrieve(conversation.question, user_id=principal.legacy_owner_key,
                                         persona_id=actor) for actor in conversation.personas]
        # Intersection is by authoritative ID, not similar wording or search rank.
        groups = [tuple(ctx.shared) + (tuple(ctx.persona) if conversation.kind == "private" else ()) for ctx in contexts]
        common = set.intersection(*(set(item.id for item in group) for group in groups))
        sources, remaining = [], 2000
        for item in groups[0]:
            if item.id not in common or len(item.content) > min(900, remaining):
                continue
            source = self._source(principal, item)
            if self.validate(principal, source) and set(conversation.personas).issubset(source.allowed_personas):
                sources.append(source)
                remaining -= len(source.text)
        return tuple(sources)

    def _source(self, principal: Principal, item) -> Source:
        data = asdict(item)
        data.pop("score", None)
        version = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        actors = self.personas.ids() if item.scope == MemoryScope.SHARED else (item.persona_id,)
        journal = self.service.journal
        if item.metadata.get("journal_managed") and journal is not None:
            actors = tuple(actor for actor in actors if actor in journal.policy.mentors)
        return Source(id=item.id, owner_id=principal.id, version=version, text=item.content,
                      kind="memory", allowed_personas=tuple(actors), origin="confirmed_memory")

    def validate(self, principal: Principal, source: Source) -> bool:
        if self.service is None or source.kind != "memory" or source.owner_id != principal.id:
            return False
        try:
            item = self.service.backend.get(source.id)
            if item.user_id != principal.legacy_owner_key or item.status != MemoryStatus.ACTIVE:
                return False
            if item.metadata.get("privacy", "cloud") != "cloud":
                return False
            if self.service.journal is not None and not self.service.journal.can_send(item):
                return False
            if item.metadata.get("journal_managed") and self.service.journal is None:
                return False
            return self._source(principal, item) == source
        except Exception:
            return False
