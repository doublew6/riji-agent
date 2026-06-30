import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from riji_agent.agent.loop import AgentLimits, AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.journal.index import JournalIndex
from riji_agent.models.types import AssistantTurn, ToolCall
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService


class FakeProvider:
    """Replays scripted assistant turns and records what it was sent."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, tools) -> AssistantTurn:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(content="(no more turns)")


def _tool_turn(name: str, args: Dict[str, Any], call_id: str = "c1") -> AssistantTurn:
    return AssistantTurn(
        content=None, tool_calls=(ToolCall(call_id, name, json.dumps(args, ensure_ascii=False)),)
    )


def _ctx() -> ToolContext:
    return ToolContext(request_id="req-1", session_id="s1", feishu_user_id="ou_1", persona_id="p1")


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    root = tmp_path / "riji"
    (root / "daily").mkdir(parents=True)
    (root / "daily" / "2026-06-24.md").write_text(
        "---\ndate: 2026-06-24\n---\n# 2026-06-24\n项目进展评审通过。\n", encoding="utf-8"
    )
    index = JournalIndex(database_path=tmp_path / "d" / "i.sqlite3", journal_root=root)
    index.build_index()
    return ToolRegistry(RetrievalService(index))


def test_loop_runs_tool_then_answers(registry: ToolRegistry) -> None:
    provider = FakeProvider([
        _tool_turn("search_journal", {"query": "项目进展"}),
        AssistantTurn(content="日记事实：项目进展评审通过 [[riji/daily/2026-06-24]]。"),
    ])
    result = AgentRunner(provider, registry).run(_ctx(), "项目进展如何？")

    assert "评审通过" in result.answer
    assert result.sources == ("riji/daily/2026-06-24",)
    assert result.tool_calls == 1
    assert result.exceeded_rounds is False
    assert result.audit[0].ok is True


def test_loop_supports_multiple_rounds(registry: ToolRegistry) -> None:
    provider = FakeProvider([
        _tool_turn("search_journal", {"query": "项目进展"}, "a"),
        _tool_turn(
            "timeline",
            {"topic": "项目进展", "date_from": "2026-06-01", "date_to": "2026-06-30", "granularity": "month"},
            "b",
        ),
        AssistantTurn(content="完成。"),
    ])
    result = AgentRunner(provider, registry).run(_ctx(), "梳理一下时间线")
    assert result.tool_calls == 2
    assert result.rounds == 3


def test_unknown_tool_is_not_executed(registry: ToolRegistry) -> None:
    provider = FakeProvider([
        _tool_turn("delete_everything", {}),
        AssistantTurn(content="无法执行该操作。"),
    ])
    result = AgentRunner(provider, registry).run(_ctx(), "删除我的日记")
    assert result.audit[0].ok is False
    assert result.audit[0].error == "unknown_tool"
    assert result.sources == ()


def test_tool_error_is_fed_back_to_model(registry: ToolRegistry) -> None:
    provider = FakeProvider([
        _tool_turn("read_note", {"source_id": "riji/daily/2026-06-24"}),
        AssistantTurn(content="需要先检索。"),
    ])
    result = AgentRunner(provider, registry).run(_ctx(), "读那篇日记")

    assert result.audit[0].error == "no_evidence"
    # the error payload is fed back as a tool message before the model's 2nd turn
    second_call_messages = provider.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
    assert "no_evidence" in tool_messages[-1]["content"]


def test_invalid_json_arguments_are_handled(registry: ToolRegistry) -> None:
    bad = AssistantTurn(content=None, tool_calls=(ToolCall("c1", "search_journal", "{not json"),))
    provider = FakeProvider([bad, AssistantTurn(content="参数有误。")])
    result = AgentRunner(provider, registry).run(_ctx(), "查一下")
    assert result.audit[0].error == "invalid_arguments"


def test_exceeding_max_rounds_forces_a_final_answer(registry: ToolRegistry) -> None:
    provider = FakeProvider([
        _tool_turn("search_journal", {"query": "项目进展"}, "a"),
        _tool_turn("search_journal", {"query": "项目进展"}, "b"),
        AssistantTurn(content="被迫给出的最终答案。"),
    ])
    result = AgentRunner(provider, registry, limits=AgentLimits(max_rounds=2)).run(_ctx(), "?")

    assert result.exceeded_rounds is True
    assert result.rounds == 2
    assert result.answer == "被迫给出的最终答案。"
    assert provider.calls[-1]["tools"] == []  # final call disables tools


def test_prior_history_is_injected_before_the_question(registry: ToolRegistry) -> None:
    provider = FakeProvider([AssistantTurn(content="记得，你上次问的是项目进展。")])
    history = [
        {"role": "user", "content": "项目进展如何？"},
        {"role": "assistant", "content": "评审通过。"},
    ]
    AgentRunner(provider, registry).run(_ctx(), "那接下来呢？", history=history)

    sent = provider.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    # history sits between system and the current question, in order
    assert sent[1] == {"role": "user", "content": "项目进展如何？"}
    assert sent[2] == {"role": "assistant", "content": "评审通过。"}
    assert sent[3] == {"role": "user", "content": "那接下来呢？"}


def test_history_is_bounded_by_count_and_chars(registry: ToolRegistry) -> None:
    provider = FakeProvider([AssistantTurn(content="ok")])
    # 30 messages, each 500 chars: both the count cap (12) and the 4000-char
    # budget must trim oldest-first, keeping only the most recent suffix.
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" + "x" * 500}
        for i in range(30)
    ]
    AgentRunner(provider, registry).run(_ctx(), "现在呢？", history=history)

    replayed = [m for m in provider.calls[0]["messages"][1:-1]]
    assert len(replayed) <= 12
    assert sum(len(m["content"]) for m in replayed) <= 4000 + 504  # last kept may cross budget
    # newest content is retained, oldest dropped
    assert any("m29" in m["content"] for m in replayed)
    assert all("m0x" not in m["content"] for m in replayed)


def test_blank_and_foreign_roles_are_dropped_from_history(registry: ToolRegistry) -> None:
    provider = FakeProvider([AssistantTurn(content="ok")])
    history = [
        {"role": "user", "content": "  "},  # blank
        {"role": "tool", "content": "internal"},  # not replayed
        {"role": "assistant", "content": "有效内容"},
    ]
    AgentRunner(provider, registry).run(_ctx(), "继续", history=history)

    replayed = provider.calls[0]["messages"][1:-1]
    assert replayed == [{"role": "assistant", "content": "有效内容"}]


def test_tool_call_budget_is_enforced(registry: ToolRegistry) -> None:
    two_calls = AssistantTurn(
        content=None,
        tool_calls=(
            ToolCall("a", "search_journal", json.dumps({"query": "项目进展"})),
            ToolCall("b", "search_journal", json.dumps({"query": "项目进展"})),
        ),
    )
    provider = FakeProvider([two_calls, AssistantTurn(content="done")])
    result = AgentRunner(provider, registry, limits=AgentLimits(max_tool_calls=1)).run(_ctx(), "?")

    assert result.tool_calls == 1
    assert any(e.error == "tool_budget_exceeded" for e in result.audit)
