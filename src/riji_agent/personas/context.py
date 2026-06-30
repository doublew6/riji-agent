"""Assemble the context a persona is allowed to see.

Shared facts (confirmed memories, preferences) and this persona's own session
history go in. Unconfirmed candidates and other personas' history never do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

from riji_agent.memory.models import ConfirmedMemory, SessionMessage
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.models import Persona
from riji_agent.personas.registry import PersonaRegistry

# Bound how many prior session messages are replayed into the model. The loop
# applies a further character budget; this just caps the rows we load and pass
# along, in line with the egress-minimisation rule.
HISTORY_TURN_LIMIT = 12


@dataclass(frozen=True)
class AssembledContext:
    persona: Persona
    system_prompt: str
    history: Tuple[SessionMessage, ...]
    shared_memories: Tuple[ConfirmedMemory, ...]
    preferences: Mapping[str, str]


def _render_shared(memories, preferences) -> str:
    parts = []
    if memories:
        lines = "\n".join(f"- {m.content}" for m in memories)
        parts.append("已确认的长期记忆（跨导师共享）：\n" + lines)
    if preferences:
        lines = "\n".join(f"- {k}: {v}" for k, v in preferences.items())
        parts.append("用户偏好：\n" + lines)
    return "\n\n".join(parts)


def build_context(
    store: MemoryStore,
    registry: PersonaRegistry,
    *,
    user_id: str,
    persona_id: str,
    chat_id: str,
    history_limit: int = HISTORY_TURN_LIMIT,
) -> AssembledContext:
    persona = registry.get(persona_id)
    memories = store.list_confirmed_memories(user_id)  # shared
    preferences = store.get_preferences(user_id)  # shared
    history = store.get_session_history(  # persona-private, bounded
        user_id, persona_id, chat_id, limit=history_limit
    )

    prompt_parts = [persona.system_prompt, persona.answer_boundaries]
    shared = _render_shared(memories, preferences)
    if shared:
        prompt_parts.append(shared)
    system_prompt = "\n\n".join(prompt_parts)

    return AssembledContext(
        persona=persona,
        system_prompt=system_prompt,
        history=tuple(history),
        shared_memories=tuple(memories),
        preferences=preferences,
    )
