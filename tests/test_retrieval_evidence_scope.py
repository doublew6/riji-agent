"""Request/observation boundaries, not a semantic score for scripted answers."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from riji_agent.agent.evidence import JOURNAL_EVIDENCE_BOUNDARIES
from riji_agent.agent.loop import AgentLimits, AgentRunner, SYSTEM_PROMPT
from riji_agent.agent.tools import ToolRegistry
from riji_agent.hermes.responder import AgentResponder
from riji_agent.journal.index import JournalIndex
from riji_agent.models.types import AssistantTurn, LLMError
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import RetrievalLimits, ToolContext
from riji_agent.retrieval.service import RetrievalService
from test_agent_loop import FakeProvider, _tool_turn


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[tuple[ToolRegistry, JournalIndex, Path]]:
    root = tmp_path / "riji"
    (root / "daily").mkdir(parents=True)
    for day, body, private in (
        (23, "Had lunch with a friend.", False),
        (24, "Orion meeting completed. Read a book afterwards.", False),
        (25, "Orion meeting was rescheduled.", False),
        (26, "Orion PRIVATE_SYNTHETIC_CANARY", True),
    ):
        (root / "daily" / f"2026-06-{day}.md").write_text(
            f"---\ndate: 2026-06-{day}\nprivate: {str(private).lower()}\n---\n{body}\n",
            encoding="utf-8",
        )
    index = JournalIndex(tmp_path / "index.sqlite3", root)
    index.build_index()
    try:
        yield ToolRegistry(RetrievalService(index)), index, root
    finally:
        index.close()


def context() -> ToolContext:
    return ToolContext("scope-request", "scope-session", "synthetic-user", "gentle_reviewer")


def _observation(provider: FakeProvider) -> dict[str, Any]:
    messages = provider.calls[-1]["messages"]
    return json.loads(next(item["content"] for item in messages if item["role"] == "tool"))


@pytest.mark.parametrize("custom_prompt", [None, "Use this synthetic persona's concise style."])
def test_existing_entry_with_no_keyword_match_has_scoped_observation(journal: Any, custom_prompt: str | None) -> None:
    registry, _, root = journal
    query = {"query": "Orion", "date_from": "2026-06-23", "date_to": "2026-06-23"}
    provider = FakeProvider([_tool_turn("search_journal", query), AssistantTurn("No returned topic evidence.")])
    result = AgentRunner(provider, registry, system_prompt=custom_prompt).run(context(), "When was the meeting?")
    sent = provider.calls[0]["messages"][0]["content"]
    assert sent.startswith(custom_prompt or SYSTEM_PROMPT)
    assert sent.endswith(JOURNAL_EVIDENCE_BOUNDARIES)
    payload = _observation(provider)
    assert (root / "daily" / "2026-06-23.md").exists()
    assert payload["items"] == [] and payload["truncated"] is False
    assert payload["evidence_scope"]["journal_completeness"] == "not_established"
    assert "missing entries" in payload["evidence_scope"]["interpretation"]
    assert result.sources == () and result.tool_calls == 1 and len(provider.calls) == 2


@pytest.mark.parametrize("persona_id", ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"])
def test_production_responder_keeps_persona_prompt_and_evidence_boundary(journal: Any, persona_id: str) -> None:
    registry, _, _ = journal
    persona = PersonaRegistry().get(persona_id)
    prompt = persona.system_prompt + "\n" + persona.answer_boundaries
    provider = FakeProvider([_tool_turn("search_journal", {"query": "Orion"}),
                             AssistantTurn("Meeting completed. [[riji/daily/2026-06-24]]")])
    answer = AgentResponder(provider, registry).respond(
        replace(context(), persona_id=persona_id), prompt, (), "Review the meeting.", persona.allowed_tools)
    sent = provider.calls[-1]["messages"][0]["content"]
    assert sent.startswith(prompt) and sent.endswith(JOURNAL_EVIDENCE_BOUNDARIES)
    assert "[[riji/daily/2026-06-24]]" in answer
    assert {item["date"] for item in _observation(provider)["items"]} == {"2026-06-24", "2026-06-25"}
    assert "PRIVATE_SYNTHETIC_CANARY" not in json.dumps(provider.calls)


@pytest.mark.parametrize("top_k", [1, 999])
def test_result_cap_is_not_exhaustive_and_does_not_expand_search(journal: Any, monkeypatch: Any, top_k: int) -> None:
    _, index, _ = journal
    registry = ToolRegistry(RetrievalService(index, limits=RetrievalLimits(max_top_k=1)))
    calls = []
    original = index.search

    def search(query: str, **kwargs: Any) -> Any:
        calls.append((query, kwargs))
        return original(query, **kwargs)

    def forbidden_scan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A bounded topic search must not enumerate or rebuild the vault")

    monkeypatch.setattr(index, "search", search)
    monkeypatch.setattr(index, "list_notes", forbidden_scan)
    monkeypatch.setattr(index, "build_index", forbidden_scan)
    provider = FakeProvider([_tool_turn("search_journal", {"query": "Orion", "top_k": top_k}), AssistantTurn("Answer.")])
    result = AgentRunner(provider, registry).run(context(), "Review Orion.")
    payload = _observation(provider)
    assert len(payload["items"]) == 1 and payload["truncated"] is False
    assert payload["evidence_scope"]["journal_completeness"] == "not_established"
    assert len(calls) == 2  # The existing pre-send guard repeats the same bounded query.
    assert all(query == "Orion" and kwargs["limit"] == 1 and kwargs["include_private"] is False
               for query, kwargs in calls)
    assert result.tool_calls == 1 and len(provider.calls) == 2


@pytest.mark.parametrize("tool,args,selection", [
    ("search_journal", {"query": "Orion"}, "query_filtered_snippets"),
    ("timeline", {"topic": "Orion", "date_from": "2026-06-23", "date_to": "2026-06-26"}, "topic_filtered_timeline"),
    ("find_before_after", {"date": "2026-06-23", "days": 1, "topic": "Orion"}, "bounded_date_window"),
    ("find_before_after", {"date": "2026-06-23", "days": 1}, "bounded_date_window"),
    ("find_before_after", {"date": "2026-06-23", "days": 1, "topic": "  "}, "bounded_date_window"),
    ("list_periods", {"kind": "daily"}, "bounded_visible_metadata"),
])
def test_every_journal_search_observation_states_its_scope(journal: Any, tool: str, args: dict, selection: str) -> None:
    registry, _, _ = journal
    invocation = registry.invoke(context(), tool, json.dumps(args))
    assert invocation.ok and invocation.before_send is not None
    invocation.before_send()
    scope = invocation.payload["evidence_scope"]
    assert scope["selection"] == selection and scope["journal_completeness"] == "not_established"
    assert "PRIVATE_SYNTHETIC_CANARY" not in json.dumps(invocation.payload)
    assert "riji/daily/2026-06-26" not in invocation.source_ids
    if tool == "timeline":
        assert "2026-06-23" in invocation.payload["empty_periods"]
        assert "not missing journal entries" in scope["interpretation"]
    if tool == "find_before_after" and args.get("topic", "").strip():
        assert invocation.payload["on"] == []
    elif tool == "find_before_after":
        assert all(item["snippet"] == "" for group in ("before", "on", "after")
                   for item in invocation.payload[group])
        assert "metadata only" in scope["interpretation"]


def test_metadata_cap_has_no_completeness_signal(journal: Any) -> None:
    _, index, _ = journal
    registry = ToolRegistry(RetrievalService(index, limits=RetrievalLimits(max_periods=1)))
    invocation = registry.invoke(context(), "list_periods", '{"kind":"daily"}')
    assert len(invocation.payload["items"]) == 1 and "truncated" not in invocation.payload
    assert invocation.payload["evidence_scope"]["journal_completeness"] == "not_established"
    assert "not proof that no entry exists" in invocation.payload["evidence_scope"]["interpretation"]


@pytest.mark.parametrize("body_limit", [20, 4000])
def test_read_scope_preserves_body_limit_and_source_gate(journal: Any, body_limit: int) -> None:
    _, index, _ = journal
    registry = ToolRegistry(RetrievalService(index, limits=RetrievalLimits(read_note_max_chars=body_limit)))
    args = json.dumps({"source_id": "riji/daily/2026-06-24"})
    denied = registry.invoke(context(), "read_note", args)
    assert denied.error == "no_evidence" and "evidence_scope" not in denied.payload
    registry.invoke(context(), "search_journal", '{"query":"Orion"}')
    read = registry.invoke(context(), "read_note", args)
    assert read.ok and len(read.payload["body"]) <= body_limit
    assert read.payload["truncated"] is (body_limit == 20)
    assert read.payload["evidence_scope"]["selection"] == "one_source_permitted_body"
    assert read.source_ids == ("riji/daily/2026-06-24",)
    denied = registry.invoke(replace(context(), session_id="other-session"), "read_note", args)
    assert denied.error == "no_evidence"


def test_empty_byte_limited_result_keeps_its_truncation_signal(journal: Any) -> None:
    _, index, _ = journal
    registry = ToolRegistry(RetrievalService(index, limits=RetrievalLimits(max_total_snippet_chars=1)))
    invocation = registry.invoke(context(), "search_journal", '{"query":"Orion"}')
    assert invocation.payload["items"] == [] and invocation.payload["truncated"] is True
    assert invocation.payload["evidence_scope"]["journal_completeness"] == "not_established"


def test_permissions_and_source_revalidation_remain_enforced(journal: Any) -> None:
    registry, _, root = journal
    denied = registry.invoke(replace(context(), chat_type="group"), "search_journal", '{"query":"Orion"}')
    assert denied.error == "tool_not_allowed" and "evidence_scope" not in denied.payload
    invocation = registry.invoke(context(), "search_journal", '{"query":"Orion"}')
    path = root / "daily" / "2026-06-24.md"
    path.write_text(path.read_text().replace("private: false", "private: true"))
    with pytest.raises(LLMError, match="chat_context_changed"):
        invocation.before_send()


def test_forced_final_answer_keeps_scope_without_extra_tool_permissions(journal: Any) -> None:
    registry, _, _ = journal
    provider = FakeProvider([_tool_turn("search_journal", {"query": "UnmatchedTopic"}), AssistantTurn("Evidence remains limited.")])
    result = AgentRunner(provider, registry, limits=AgentLimits(max_rounds=1)).run(context(), "Question")
    assert result.exceeded_rounds and result.tool_calls == 1 and len(provider.calls) == 2
    assert provider.calls[-1]["tools"] == []
    assert provider.calls[-1]["messages"][0]["content"].endswith(JOURNAL_EVIDENCE_BOUNDARIES)
    assert _observation(provider)["evidence_scope"]["journal_completeness"] == "not_established"
    assert result.answer == "Evidence remains limited."
