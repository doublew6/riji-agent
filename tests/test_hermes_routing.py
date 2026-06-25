import pytest

from riji_agent.hermes.routing import route_persona
from riji_agent.personas.models import UnknownPersonaError
from riji_agent.personas.registry import PersonaRegistry

registry = PersonaRegistry()


def test_command_switches_and_persists() -> None:
    route = route_persona("/导师 直率教练 我在吗", registry=registry, current_persona="gentle_reviewer")
    assert route.persona_id == "blunt_coach"
    assert route.text == "我在吗"
    assert route.persist is True


def test_command_accepts_persona_id() -> None:
    route = route_persona("/persona future_self", registry=registry, current_persona="gentle_reviewer")
    assert route.persona_id == "future_self"
    assert route.persist is True


def test_at_mention_is_one_shot() -> None:
    route = route_persona("@未来的我 给点建议", registry=registry, current_persona="gentle_reviewer")
    assert route.persona_id == "future_self"
    assert route.text == "给点建议"
    assert route.persist is False


def test_plain_text_keeps_current_persona() -> None:
    route = route_persona("今天过得怎样", registry=registry, current_persona="blunt_coach")
    assert route.persona_id == "blunt_coach"
    assert route.persist is False


def test_unknown_persona_raises() -> None:
    with pytest.raises(UnknownPersonaError):
        route_persona("/导师 不存在的导师", registry=registry, current_persona="gentle_reviewer")
