from pathlib import Path

import pytest

from riji_agent.memory.store import MemoryStore
from riji_agent.personas.context import build_context
from riji_agent.personas.registry import PersonaRegistry


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(database_path=tmp_path / "data" / "mem.sqlite3")
    yield s
    s.close()


@pytest.fixture
def registry() -> PersonaRegistry:
    return PersonaRegistry()


def test_context_includes_shared_memory_and_preferences(store, registry) -> None:
    cid = store.add_candidate("u1", "gentle_reviewer", "用户在写一本书")
    store.confirm_candidate(cid)
    store.set_preference("u1", "language", "中文")

    ctx = build_context(store, registry, user_id="u1", persona_id="blunt_coach", chat_id="c1")

    assert "用户在写一本书" in ctx.system_prompt  # confirmed memory is shared across personas
    assert "中文" in ctx.system_prompt
    assert ctx.persona.persona_id == "blunt_coach"


def test_context_excludes_unconfirmed_candidates(store, registry) -> None:
    store.add_candidate("u1", "gentle_reviewer", "未确认的私密观察")
    ctx = build_context(store, registry, user_id="u1", persona_id="gentle_reviewer", chat_id="c1")
    assert "未确认的私密观察" not in ctx.system_prompt
    assert ctx.shared_memories == ()


def test_switching_persona_does_not_leak_session_history(store, registry) -> None:
    store.append_message("u1", "gentle_reviewer", "c1", "user", "温柔会话内容")

    gentle = build_context(store, registry, user_id="u1", persona_id="gentle_reviewer", chat_id="c1")
    blunt = build_context(store, registry, user_id="u1", persona_id="blunt_coach", chat_id="c1")

    assert len(gentle.history) == 1
    assert blunt.history == ()  # no cross-persona leak


def test_context_system_prompt_starts_from_persona_config(store, registry) -> None:
    ctx = build_context(store, registry, user_id="u1", persona_id="future_self", chat_id="c1")
    assert ctx.system_prompt.startswith(registry.get("future_self").system_prompt)
    assert "private" in ctx.system_prompt  # shared boundaries are always present
