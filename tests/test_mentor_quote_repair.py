"""Offline regression for copying an actual next-step sentence as a body quote."""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from riji_agent.mentors.comparison import COMPARISON_QUOTE_DESCRIPTION, validate_comparison
from riji_agent.mentors.generation import ModelGeneration, STAGES, output_schema
from riji_agent.mentors.models import Artifact, Generation, MentorError
from riji_agent.models.types import AssistantTurn
from riji_agent.personas.registry import PersonaRegistry
from test_mentor_comparison import request, result
from test_mentor_discussions import prepare, system  # noqa: F401

_GENTLE_TEXT = "今晚优先按时休息，把演示收在够用且稳定的位置。"
_BLUNT_TEXT = "核心判断：按时休息，不要继续完善到深夜。建议：给自己一个硬性截止，最多再花30分钟只做低风险整理。"
_NEXT_STEP = "若选继续，只给30分钟低风险整理，设置闹钟，不做结构调整。"
_REJECTED_TEXT = "REJECTED_SYNTHETIC_OUTPUT_CANARY"


@pytest.mark.parametrize("field", ["next_steps", "claims", "uncertainties"])
def test_same_mentor_metadata_is_not_accepted_as_body_evidence(field: str) -> None:
    current = request((_GENTLE_TEXT, _BLUNT_TEXT))
    second = current.previous[1].model_copy(update={field: (_NEXT_STEP,)})
    current = current.model_copy(update={"previous": (current.previous[0], second)})
    assert _NEXT_STEP in getattr(second, field) and _NEXT_STEP not in second.text
    data = result(current, "compatible").model_dump()
    data["comparison_findings"][0]["stances"][1]["quote"] = _NEXT_STEP
    output = Generation.model_validate(data)
    with pytest.raises(MentorError, match="comparison_quote_unsupported"):
        validate_comparison(output, current)
    data["comparison_findings"][0]["stances"][1]["quote"] = _BLUNT_TEXT
    validate_comparison(Generation.model_validate(data), current)


def test_quote_schema_and_initial_instructions_identify_the_body_field() -> None:
    schema = output_schema(request())
    quote = schema["$defs"]["ComparisonStance"]["properties"]["quote"]
    assert quote["description"] == COMPARISON_QUOTE_DESCRIPTION
    assert quote["minLength"] == 1 and quote["maxLength"] == 1200
    assert "continuous substring" in quote["description"]
    for field in ("claims", "next_steps", "uncertainties"):
        assert field in quote["description"] and field in STAGES["comparison"]
    assert "previous artifact.text" in STAGES["comparison"]


class QuoteProvider:
    """Return a valid schema with an invalid quote, then optionally repair it."""

    def __init__(self, repair_succeeds: bool) -> None:
        self.repair_succeeds = repair_succeeds
        self.calls: list[list[dict[str, Any]]] = []

    def complete_with_guard(self, messages: list[dict], tools: list,
                            *, before_send: Callable[[], None]) -> AssistantTurn:
        before_send()
        self.calls.append(json.loads(json.dumps(messages)))
        payload = json.loads(messages[-1]["content"])
        opinions = [item for item in payload["previous"] if item["kind"] == "opinion"]
        first, second = opinions
        repaired = len(self.calls) == 2 and self.repair_succeeds
        finding = {
            "decision": "Whether to rest tonight", "shared_condition": "The draft is ready",
            "relationship": "compatible", "rationale": "Both recommend rest with bounded preparation.",
            "stances": [{"artifact_id": first["id"], "quote": first["text"]},
                        {"artifact_id": second["id"], "quote": second["text"] if repaired else second["next_steps"][0]}],
        }
        output = Generation.model_validate({"text": "A grounded comparison." if repaired else _REJECTED_TEXT,
            "debate_needed": False, "source_refs": [first["id"], second["id"]], "comparison_findings": [finding]})
        return AssistantTurn(output.model_dump_json())


def _prepare_body_and_metadata(system: Any) -> tuple[Any, Any]:
    service, worker, _, _, _, _, *_ = system
    conversation = prepare(system)
    for _ in range(2):
        worker.run_one(conversation.id)
    opinions = [item for item in service.store.list("artifact", conversation.id, Artifact) if item.kind == "opinion"]
    assert len(opinions) == 2
    with service.store.transaction() as db:
        for item, text in zip(opinions, (_GENTLE_TEXT, _BLUNT_TEXT)):
            updated = item.model_copy(update={"text": text, "next_steps": (_NEXT_STEP,)})
            service.store.put(db, "artifact", updated, conversation.id)
    return conversation, opinions


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_actionable_body_quote_repair_is_once_and_charged(system: Any, repair_succeeds: bool) -> None:
    service, worker, _, _, _, principal, *_ = system
    conversation, opinions = _prepare_body_and_metadata(system)
    provider = QuoteProvider(repair_succeeds)
    worker.generator = ModelGeneration(provider, PersonaRegistry())
    assert worker.run_one(conversation.id)
    assert len(provider.calls) == 2
    first_instruction = provider.calls[0][0]["content"]
    second_instruction = provider.calls[1][0]["content"]
    assert "上次输出未通过" not in first_instruction
    repair = second_instruction.split("上次输出未通过", 1)[1]
    for required in ("comparison_quote_unsupported", "stance.artifact_id", "artifact.text", "连续子串",
                     "claims", "next_steps", "uncertainties", "不要为修复引文捏造共识或冲突"):
        assert required in repair
    assert _REJECTED_TEXT not in json.dumps(provider.calls[1])
    assert provider.calls[0][-1]["content"] == provider.calls[1][-1]["content"]
    assert service.budgets.status(conversation)["total_requests"] == 4  # Two opinions plus two comparison attempts.
    artifacts = service.store.list("artifact", conversation.id, Artifact)
    comparisons = [item for item in artifacts if item.kind == "comparison"]
    if repair_succeeds:
        assert len(comparisons) == 1
        quotes = comparisons[0].comparison_findings[0].stances
        assert {item.artifact_id for item in quotes} == {item.id for item in opinions}
        assert quotes[1].quote == _BLUNT_TEXT and comparisons[0].debate_needed is False
    else:
        assert comparisons == [] and service.get(conversation.id, principal.id).status == "interrupted"
        assert not worker.run_one(conversation.id) and len(provider.calls) == 2
