"""Mentor harness contracts; controlled outputs never imply semantic quality."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from riji_agent.models.types import AssistantTurn
from scripts.evalmesh_support.mentors import evaluate

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / "evals/agent-v1/mentors.jsonl").read_text().splitlines()]


class ControlledProvider:
    def __init__(self, debate_needed: bool = True, invalid: bool = False) -> None:
        self.debate_needed = debate_needed
        self.invalid = invalid
        self.messages: list[list[dict[str, Any]]] = []

    def complete(self, messages, tools) -> AssistantTurn:
        assert tools == []
        self.messages.append(deepcopy(messages))
        if self.invalid:
            return AssistantTurn(content="not valid JSON")
        payload = json.loads(messages[-1]["content"])
        prefix = "仅返回符合此schema的JSON对象，无Markdown围栏。source_refs只能使用输入给出的来源id。\n"
        schema = json.JSONDecoder().raw_decode(messages[0]["content"].split(prefix, 1)[1])[0]
        targets = schema["properties"]["responds_to"]["items"].get("enum", [])
        result = {
            "text": "Synthetic bounded response for adapter verification only.",
            "source_refs": [item["id"] for item in payload["background"]],
            "responds_to": targets[:1] if payload["stage"] == "debate" else [],
            "debate_needed": self.debate_needed if payload["stage"] == "comparison" else None,
            "uncertainties": ["Synthetic unresolved condition."],
            "next_steps": ["Synthetic optional step."],
        }
        if payload["stage"] == "opinion":
            number = sum(json.loads(row[-1]["content"])["stage"] == "opinion" for row in self.messages)
            result["text"] += f" Choose only option {number}." if self.debate_needed else " Try the same compatible option."
        if payload["stage"] == "comparison":
            opinions = [item for item in payload["previous"] if item["kind"] == "opinion"][:2]
            result["comparison_findings"] = [{
                "decision": "Select exactly one option", "shared_condition": "Only one is possible",
                "relationship": "conflict" if self.debate_needed else "compatible",
                "stances": [{"artifact_id": item["id"], "quote": item["text"]} for item in opinions],
                "rationale": "Different exclusive options" if self.debate_needed else "Both suggest the same option",
            }]
            result["source_refs"].extend(item["id"] for item in opinions)
        return AssistantTurn(content=json.dumps(result))


def by_scenario(scenario: str) -> dict[str, Any]:
    return next(case for case in CASES if case["input"]["scenario"] == scenario)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_authored_scenarios_measure_actual_service_state(case):
    result = evaluate(case["input"], ControlledProvider())
    observed = result["output"]["observed"]
    expected = case["expected"]["observed"]
    assert observed == expected
    assert result["output"]["semantic_review"] == "pending"
    assert result["output"]["error_code"] == ""
    assert result["metrics"]["model_requests"] == result["metrics"]["charged_requests"]
    assert all(item["status"] == "succeeded" for item in result["output"]["step_records"])


def test_no_disagreement_legitimately_skips_debate_without_failing_contract():
    case = by_scenario("roundtable-debate-sleep")
    result = evaluate(case["input"], ControlledProvider(debate_needed=False))
    assert result["output"]["observed"] == case["expected"]["observed"]
    stages = [item["stage"] for item in result["output"]["requests"]]
    assert stages == ["opinion", "opinion", "comparison", "synthesis"]


def test_invalid_model_output_is_repaired_once_then_recorded_as_failure():
    case = by_scenario("single-blunt_coach-bounded")
    provider = ControlledProvider(invalid=True)
    result = evaluate(case["input"], provider)
    assert len(provider.messages) == 2
    assert result["output"]["status"] == "interrupted"
    assert result["output"]["blocked"] == "invalid_generation"
    assert result["output"]["observed"] != case["expected"]["observed"]
    assert result["metrics"]["repair_attempts"] == 1
    assert not [item for item in result["output"]["artifacts"] if item["kind"] != "user"]


def test_expected_and_rubric_never_reach_provider_or_production_configuration(monkeypatch):
    import riji_agent.config

    def forbidden(*args, **kwargs):
        raise AssertionError("production settings must not be loaded")

    monkeypatch.setattr(riji_agent.config, "load_settings", forbidden)
    case = deepcopy(by_scenario("single-blunt_coach-bounded")["input"])
    case["expected"] = {"secret_answer": "EVAL_EXPECTED_SENTINEL"}
    case["rubric"] = "EVAL_RUBRIC_SENTINEL"
    provider = ControlledProvider()
    result = evaluate(case, provider)
    sent = json.dumps(provider.messages)
    assert "EVAL_EXPECTED_SENTINEL" not in sent and "EVAL_RUBRIC_SENTINEL" not in sent
    assert result["output"]["status"] == "completed"


def test_persistent_context_uses_correction_and_marks_old_ai_as_superseded():
    case = by_scenario("persistent-correction-target")
    result = evaluate(case["input"], ControlledProvider())["output"]
    last = result["requests"][-1]
    summary = last["working_summary"]
    assert summary["correction_version"] == 1
    assert [item["text"] for item in summary["items"] if item["kind"] == "user_statement"] == [
        case["input"]["steps"][0]["text"]
    ]
    assert not [item for item in summary["items"] if item["kind"] == "ai_advice"]
    assert len(result["summaries"]) > 1 and len(result["runs"]) == 2


def test_reanalysis_filters_shared_ai_and_retains_explicit_user_plan():
    result = evaluate(by_scenario("persistent-reanalysis-shared-ai")["input"], ControlledProvider())
    requests = result["output"]["requests"]
    assert {item["id"] for item in requests[0]["background"]} == {"shared-plan-v1", "shared-ai-v1"}
    assert [item["id"] for item in requests[-1]["background"]] == ["shared-plan-v1"]
    assert requests[-1]["reanalyze"]


def test_case_state_is_fresh_and_private_outputs_retain_source_evidence():
    case = by_scenario("roundtable-debate-source")["input"]
    first = evaluate(case, ControlledProvider())
    second = evaluate(case, ControlledProvider())
    assert first["output"]["observed"] == second["output"]["observed"]
    assert first["output"]["runs"][0]["id"] != second["output"]["runs"][0]["id"]
    artifacts = first["output"]["artifacts"]
    assert any("feedback-practice-v1" in item["source_refs"] for item in artifacts)
    assert all(item["origin_kind"] == "ai_discussion" for item in artifacts if item["kind"] != "user")


def test_corpus_counts_and_review_provenance_are_honest():
    review = json.loads((ROOT / "evals/agent-v1/mentor-review.json").read_text())
    assert len(CASES) == 44 and len({case["id"] for case in CASES}) == 44
    assert sum(case["input"]["mode"] == "private" for case in CASES) == 16
    assert sum(case["input"]["scenario"].startswith("persistent-") for case in CASES) == 12
    assert sum("smoke" in case["tags"] for case in CASES) == 4
    assert sum("acceptance-candidate" in case["tags"] for case in CASES) == 22
    assert set(review["cases"]) == {case["id"] for case in CASES}
    assert not review["independent_holdout"] and review["review_status"] == "pending"
    assert all(case["input"]["data_class"] == "synthetic" for case in CASES)
    assert all(entry["reviewer_scores"] is None for entry in review["cases"].values())


def test_unbounded_scenario_is_rejected_before_generation():
    case = deepcopy(CASES[0]["input"])
    case["steps"] = [{"kind": "continue"}] * 7
    provider = ControlledProvider()
    with pytest.raises(ValueError, match="mentor_eval_too_many_steps"):
        evaluate(case, provider)
    assert not provider.messages
