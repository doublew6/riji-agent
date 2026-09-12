"""Failure and source-boundary coverage for the synthetic EvalMesh targets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riji_agent.memory.journal_types import JournalMemoryError
from riji_agent.models.types import AssistantTurn, LLMError, ToolCall
from scripts.evalmesh_support.memory import evaluate


class ControlledProvider:
    """A contract test provider, never a substitute for quality evaluation."""

    def __init__(self, turns: list[AssistantTurn | Exception]) -> None:
        self.turns = iter(turns)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, tools: Any) -> AssistantTurn:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        turn = next(self.turns)
        if isinstance(turn, Exception):
            raise turn
        return turn


def _tool(name: str, arguments: dict[str, Any]) -> AssistantTurn:
    return AssistantTurn(None, (ToolCall("test-call", name, json.dumps(arguments)),))


def _memory(**updates: Any) -> dict[str, Any]:
    return {"family": "memory", "data_class": "synthetic", "text": "今天完成了项目演示。",
            "observed_at": "2026-09-03", "supplied_neighbors": [], **updates}


def _extraction(**updates: Any) -> AssistantTurn:
    memory = {"content": "用户完成了项目演示。", "kind": "event", "certainty": "explicit",
              "valid_from": "2026-09-03", "quotes": ["完成了项目演示"], **updates}
    return AssistantTurn(json.dumps({"complete": True, "memories": [memory]}, ensure_ascii=False))


def _retrieval(**updates: Any) -> dict[str, Any]:
    return {"family": "retrieval", "data_class": "synthetic", "question": "青杉项目演示如何？",
            "notes": [{"path": "daily/2026-09-03.md", "markdown":
                       "---\ndate: 2026-09-03\n---\n# 2026-09-03\n青杉项目演示完成了。\n"}],
            **updates}


def test_memory_uses_real_extractor_but_does_not_self_score_semantics() -> None:
    provider = ControlledProvider([_extraction()])
    result = evaluate(_memory(), provider)
    assert result["output"]["observed"]["evidence_quotes_valid"] is True
    assert result["output"]["candidates"][0]["kind"] == "event"
    assert result["metrics"]["memory_guarded_send_attempts"] == 1
    assert result["metrics"]["memory_request_chars"] > len(_memory()["text"])
    assert result["output"]["semantic_review"] == "unreviewed"
    assert result["output"]["real_mem0_retrieval"] == "not_run"
    assert "expected" not in json.dumps(provider.calls)


@pytest.mark.parametrize("response,error", [
    (_extraction(quotes=["并不存在的证据"]), "journal_invalid_evidence_quote"),
    (_extraction(certainty="inferred"), "journal_inference_must_be_observation"),
    (AssistantTurn("not-json"), "journal_invalid_model_json"),
])
def test_extractor_failure_paths_are_not_relabelled_pass(response: AssistantTurn, error: str) -> None:
    with pytest.raises(JournalMemoryError, match=error):
        evaluate(_memory(), ControlledProvider([response]))


def test_unknown_relation_target_is_rejected_by_real_relation_parser() -> None:
    neighbors = [{"id": "old-1", "content": "用户完成项目演示。", "kind": "event",
                  "valid_from": "2026-09-03"}]
    relation = AssistantTurn(json.dumps({"decisions": [
        {"index": 0, "action": "duplicate", "target_id": "invented", "reason": "Same event"},
    ]}))
    with pytest.raises(JournalMemoryError, match="journal_invalid_relation_target"):
        evaluate(_memory(supplied_neighbors=neighbors), ControlledProvider([_extraction(), relation]))


def test_ai_content_never_becomes_personal_extraction_or_model_input() -> None:
    provider = ControlledProvider([])
    result = evaluate(_memory(content_type="ai_discussion_result"), provider)
    assert result["output"]["candidates"] == []
    assert result["metrics"]["memory_guarded_send_attempts"] == 0
    assert provider.calls == []


def test_empty_extraction_remains_unreviewed_even_with_valid_contract() -> None:
    provider = ControlledProvider([AssistantTurn('{"complete":true,"memories":[]}')])
    result = evaluate(_memory(), provider)
    assert result["output"]["observed"]["status"] == "completed"
    assert result["output"]["semantic_review"] == "unreviewed"
    assert result["metrics"]["candidate_count"] == 0


def test_retrieval_uses_actual_search_then_gated_note_read() -> None:
    provider = ControlledProvider([
        _tool("search_journal", {"query": "青杉项目"}),
        _tool("read_note", {"source_id": "riji/daily/2026-09-03"}),
        AssistantTurn("日记事实：演示完成了。[[riji/daily/2026-09-03]]"),
    ])
    result = evaluate(_retrieval(), provider)
    assert result["metrics"]["tool_calls"] == 2
    assert result["output"]["observed"]["vault_unchanged"] is True
    assert result["output"]["observed"]["citations_without_retrieved_content"] == []
    observations = result["output"]["tool_observations"]
    assert observations[0]["payload"]["items"][0]["snippet"]
    assert "演示完成了" in observations[1]["payload"]["body"]
    assert result["output"]["scope"] == "agent_runner_local_fts_only"


def test_private_content_is_not_sent_to_provider_and_direct_read_is_gated() -> None:
    marker = "SYNTHETIC-PRIVATE-MARKER-47"
    private = {"path": "daily/2026-09-04.md", "markdown":
               f"---\ndate: 2026-09-04\nprivate: true\n---\n私人暗号 {marker}\n"}
    provider = ControlledProvider([
        _tool("search_journal", {"query": "私人暗号"}),
        _tool("read_note", {"source_id": "riji/daily/2026-09-04"}),
        AssistantTurn("在允许的记录里没有足够证据。"),
    ])
    result = evaluate(_retrieval(notes=[private]), provider)
    assert result["output"]["source_ids"] == []
    assert result["output"]["tool_observations"][1]["ok"] is False
    assert marker not in json.dumps(provider.calls, ensure_ascii=False)


def test_metadata_only_source_is_not_treated_as_content_support() -> None:
    provider = ControlledProvider([
        _tool("list_periods", {}),
        AssistantTurn("未读内容就声称完成演示。[[riji/daily/2026-09-03]]"),
    ])
    result = evaluate(_retrieval(), provider)
    assert result["output"]["source_ids"] == ["riji/daily/2026-09-03"]
    assert result["output"]["observed"]["citations_without_retrieved_content"] == [
        "riji/daily/2026-09-03",
    ]


def test_fabricated_citation_fails_machine_provenance_observation() -> None:
    provider = ControlledProvider([AssistantTurn("编造的事实 [[riji/daily/2020-01-01]]")])
    result = evaluate(_retrieval(), provider)
    assert result["output"]["observed"]["citations_without_retrieved_content"] == [
        "riji/daily/2020-01-01",
    ]


def test_provider_failure_closes_temporary_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.evalmesh_support import memory

    closed = []
    original = memory.JournalIndex.close

    def close(index: Any) -> None:
        closed.append(index._database_path)
        original(index)

    monkeypatch.setattr(memory.JournalIndex, "close", close)
    with pytest.raises(LLMError, match="test_provider_failure"):
        evaluate(_retrieval(), ControlledProvider([LLMError("test_provider_failure")]))
    assert len(closed) == 1
    assert not closed[0].exists()


@pytest.mark.parametrize("path", ["../../escape.md", "/tmp/escape.md", "daily/a/b.md",
                                 "daily/../escape.md", "daily\\escape.md"])
def test_fixture_paths_cannot_escape_temporary_vault(path: str) -> None:
    with pytest.raises(ValueError, match="invalid_synthetic_note_path"):
        evaluate(_retrieval(notes=[{"path": path, "markdown": "synthetic"}]), ControlledProvider([]))


def test_gold_is_not_accepted_by_target() -> None:
    with pytest.raises(ValueError, match="evaluation_answers_not_allowed_in_target_input"):
        evaluate(_memory(expected={"secret": "answer"}), ControlledProvider([]))


def test_corpus_has_separate_annotations_and_honest_scope() -> None:
    root = Path(__file__).resolve().parents[1] / "evals" / "agent-v1"
    cases = [json.loads(line) for line in (root / "memory.jsonl").read_text().splitlines()]
    review = json.loads((root / "memory-review.json").read_text())
    assert len(cases) == 36
    assert sum(row["input"]["family"] == "memory" for row in cases) == 20
    assert sum("smoke" in row["tags"] for row in cases) == 4
    assert sum("acceptance-candidate" in row["tags"] for row in cases) == 18
    assert all(row["input"]["data_class"] == "synthetic" for row in cases)
    assert all(not {"expected", "gold", "review", "rubric"} & row["input"].keys() for row in cases)
    assert {row["id"] for row in cases} == {row["case_id"] for row in review["cases"]}
    assert review["independent_holdout"] is False
    assert all(row["status"] == "unreviewed" for row in review["cases"])
