"""Persona configuration model.

A persona is fixed configuration (tone, system prompt, allowed tools, answer
boundaries). It is defined in code and is immutable, so ephemeral chat text can
never redefine a persona at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


class UnknownPersonaError(KeyError):
    """Raised when a persona id is not registered."""


@dataclass(frozen=True)
class Persona:
    persona_id: str
    name: str
    description: str
    system_prompt: str
    allowed_tools: Tuple[str, ...]
    answer_boundaries: str
    voice: Optional[str] = None
    uses_yangming: bool = False
