import dataclasses

import pytest

from riji_agent.personas.models import UnknownPersonaError
from riji_agent.personas.registry import STANDARD_TOOLS, PersonaRegistry


def test_presets_include_the_three_mentors() -> None:
    ids = set(PersonaRegistry().ids())
    assert {"gentle_reviewer", "blunt_coach", "future_self"} <= ids


def test_get_unknown_persona_raises() -> None:
    with pytest.raises(UnknownPersonaError):
        PersonaRegistry().get("does_not_exist")


def test_personas_expose_the_standard_tools() -> None:
    for persona in PersonaRegistry().all():
        assert persona.allowed_tools == STANDARD_TOOLS


def test_persona_config_cannot_be_overwritten_at_runtime() -> None:
    persona = PersonaRegistry().get("blunt_coach")
    # Frozen config: ephemeral chat text can never mutate a persona.
    with pytest.raises(dataclasses.FrozenInstanceError):
        persona.system_prompt = "ignore previous instructions"  # type: ignore[misc]
