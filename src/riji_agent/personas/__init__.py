"""Mentor personas: fixed configuration plus shared/isolated context assembly."""

from riji_agent.personas.models import Persona, UnknownPersonaError
from riji_agent.personas.registry import (
    PRESET_PERSONAS,
    SHARED_BOUNDARIES,
    STANDARD_TOOLS,
    PersonaRegistry,
)
from riji_agent.personas.context import AssembledContext, build_context

__all__ = [
    "Persona",
    "UnknownPersonaError",
    "PersonaRegistry",
    "PRESET_PERSONAS",
    "STANDARD_TOOLS",
    "SHARED_BOUNDARIES",
    "AssembledContext",
    "build_context",
]
