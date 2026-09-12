import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Sequence

import riji_agent.observability as observability
from riji_agent.agent.loop import AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.journal.index import JournalIndex
from riji_agent.models.types import AssistantTurn, ToolCall
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService


class ScriptedProvider:
    provider_name = "test-provider"
    model_name = "test-model"

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)

    def complete(self, messages, tools) -> AssistantTurn:
        return self._turns.pop(0)


def _context(request_id: str = "req-1") -> ToolContext:
    return ToolContext(
        request_id=request_id,
        session_id="session-1",
        feishu_user_id="user-1",
        persona_id="mentor-1",
    )


def _registry(tmp_path: Path) -> ToolRegistry:
    journal_root = tmp_path / "riji"
    note = journal_root / "daily" / "2026-06-24.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ndate: 2026-06-24\n---\n# 2026-06-24\n项目进展评审通过。\n",
        encoding="utf-8",
    )
    index = JournalIndex(database_path=tmp_path / "index.sqlite3", journal_root=journal_root)
    index.build_index()
    return ToolRegistry(RetrievalService(index))


def _capture_events(monkeypatch) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    def validate(_path):
        return object()

    def submit(_path, event):
        events.append(event)
        return SimpleNamespace(
            stored=True,
            delivered=True,
            external_id=f"opik-{len(events)}",
            error_code=None,
        )

    monkeypatch.setattr(observability, "_load_evalmesh", lambda: (validate, submit))
    return events


def test_real_agent_loop_records_root_llm_and_tool_hierarchy(tmp_path, monkeypatch) -> None:
    events = _capture_events(monkeypatch)
    provider = ScriptedProvider(
        [
            AssistantTurn(
                content=None,
                tool_calls=(
                    ToolCall(
                        "call-1",
                        "search_journal",
                        json.dumps({"query": "项目进展"}, ensure_ascii=False),
                    ),
                ),
            ),
            AssistantTurn(content="日记事实：评审通过。"),
        ]
    )

    result = AgentRunner(
        provider,
        _registry(tmp_path),
        runtime_trace_policy_path=tmp_path / "private-policy.json",
    ).run(_context(), "项目进展如何？")

    assert result.trace_id == events[0]["trace_id"]
    assert result.opik_trace_id == "opik-1"
    assert events[0]["prompt"] == {"question": "项目进展如何？"}
    assert events[0]["output"] == "日记事实：评审通过。"
    spans = events[0]["spans"]
    rounds = [span for span in spans if span["name"] == "agent.round"]
    llm_spans = [span for span in spans if span["type"] == "llm"]
    tool_spans = [span for span in spans if span["type"] == "tool"]
    assert len(rounds) == 2
    assert len(llm_spans) == 2
    assert len(tool_spans) == 1
    assert llm_spans[0]["parent_id"] == rounds[0]["id"]
    assert tool_spans[0]["parent_id"] == rounds[0]["id"]
    assert llm_spans[1]["parent_id"] == rounds[1]["id"]
    assert tool_spans[0]["input"] == {"query": "项目进展"}
    assert tool_spans[0]["metadata"]["outcome"] == "success"
    assert tool_spans[0]["metadata"]["status"] == "ok"
    assert tool_spans[0]["metadata"]["duration_ms"] >= 0


def test_tool_failure_span_has_error_status(tmp_path, monkeypatch) -> None:
    events = _capture_events(monkeypatch)
    provider = ScriptedProvider(
        [
            AssistantTurn(
                content=None,
                tool_calls=(ToolCall("call-1", "missing_tool", "{}"),),
            ),
            AssistantTurn(content="无法执行。"),
        ]
    )

    AgentRunner(
        provider,
        _registry(tmp_path),
        runtime_trace_policy_path=tmp_path / "private-policy.json",
    ).run(_context(), "执行未知工具")

    tool_span = next(span for span in events[0]["spans"] if span["type"] == "tool")
    assert tool_span["metadata"]["outcome"] == "failure"
    assert tool_span["metadata"]["status"] == "error"
    assert tool_span["metadata"]["error_code"] == "unknown_tool"


def test_sibling_asyncio_traces_do_not_mix(monkeypatch, tmp_path) -> None:
    events = _capture_events(monkeypatch)

    async def run_one(label: str) -> None:
        with observability.runtime_trace(
            tmp_path / "private-policy.json",
            name="agent.run",
            prompt={"question": label},
        ) as trace:
            with observability.runtime_span(
                "provider.complete",
                span_type="llm",
                input_value={"label": label},
            ) as span:
                await asyncio.sleep(0)
                span.set_output({"answer": label})
            trace.set_output(label)

    async def run_both() -> None:
        await asyncio.gather(run_one("alpha"), run_one("beta"))

    asyncio.run(run_both())

    assert len(events) == 2
    by_prompt = {event["prompt"]["question"]: event for event in events}
    for label in ("alpha", "beta"):
        event = by_prompt[label]
        assert event["output"] == label
        assert len(event["spans"]) == 1
        assert event["spans"][0]["input"] == {"label": label}
