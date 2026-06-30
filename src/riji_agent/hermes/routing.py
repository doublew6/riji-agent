"""Route a Feishu message to a persona via commands or @mentions.

- ``/导师 <name>``, ``/persona <id>``, ``/切换 <name>``: switch the current
  persona and persist it as the user's preference.
- ``@<name> ...``: use that persona for this one message only.
- otherwise: keep the user's current persona.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from riji_agent.personas.models import UnknownPersonaError
from riji_agent.personas.registry import PersonaRegistry

_COMMAND_PREFIXES = ("/导师", "/persona", "/切换")


@dataclass(frozen=True)
class PersonaRoute:
    persona_id: str
    text: str
    persist: bool  # whether to store this as the new current persona


def _name_index(registry: PersonaRegistry) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for persona in registry.all():
        index[persona.persona_id] = persona.persona_id
        index[persona.name] = persona.persona_id
    return index


def _resolve(name: str, registry: PersonaRegistry) -> str:
    persona_id = _name_index(registry).get(name.strip())
    if persona_id is None:
        raise UnknownPersonaError(name)
    return persona_id


def route_persona(text: str, *, registry: PersonaRegistry, current_persona: str) -> PersonaRoute:
    stripped = text.strip()

    for prefix in _COMMAND_PREFIXES:
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            name, _, remaining = rest.partition(" ")
            return PersonaRoute(_resolve(name, registry), remaining.strip(), persist=True)

    if stripped.startswith("@"):
        name, _, remaining = stripped[1:].partition(" ")
        return PersonaRoute(_resolve(name, registry), remaining.strip(), persist=False)

    return PersonaRoute(current_persona, stripped, persist=False)
