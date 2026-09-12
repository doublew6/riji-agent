"""Domain boundary checks remain external to the target's observations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.evalmesh_support.boundaries import evaluate


CORPUS = Path(__file__).resolve().parents[1] / "evals/agent-v1/boundaries.jsonl"
CASES = [json.loads(line) for line in CORPUS.read_text().splitlines() if line]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_deterministic_case_matches_external_expectation(case: dict[str, Any]) -> None:
    result = evaluate(case["input"])
    assert set(result) == {"output", "metrics"}
    assert result["output"]["observed"] == case["expected"]["observed"]
    assert result["metrics"]["live_model_calls"] == 0


def test_corpus_has_distinct_paths_and_required_coverage() -> None:
    assert len(CASES) == 40
    assert len({case["input"]["scenario"] for case in CASES}) == 40
    assert len({case["id"] for case in CASES}) == 40
    assert sum("smoke" in case["tags"] for case in CASES) == 4
    for group, count in (("permissions", 24), ("save", 8), ("recovery", 8)):
        assert sum(group in case["tags"] for case in CASES) == count
    assert all(case["grader_ids"] == ["observed"] for case in CASES)
    assert all(case["input"]["data_class"] == "synthetic" for case in CASES)
    assert all("expected" not in case["input"] for case in CASES)


def test_attempts_do_not_share_journal_or_identity_state() -> None:
    for scenario in ("save_duplicate", "link_two_phase", "restart"):
        case = next(case for case in CASES if case["input"]["scenario"] == scenario)
        assert evaluate(case["input"]) == evaluate(case["input"])


def test_supplied_provider_cannot_receive_boundary_material() -> None:
    class ForbiddenProvider:
        def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Boundary scenarios cannot make a live model call")

    result = evaluate(CASES[0]["input"], ForbiddenProvider())
    assert result["metrics"]["live_model_calls"] == 0


def test_service_behavior_is_observed_instead_of_self_graded(monkeypatch: Any) -> None:
    from riji_agent.agent.tools import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "_authorized", staticmethod(lambda *_: True))
    case = next(case for case in CASES if case["input"]["scenario"] == "group_draft")
    result = evaluate(case["input"])
    assert result["output"]["observed"] != case["expected"]["observed"]
    assert result["output"]["observed"]["error"] is None


@pytest.mark.parametrize("case", [
    {}, {"family": "boundary", "scenario": "cross_user", "data_class": "production"},
    {"family": "boundary", "scenario": "missing", "data_class": "synthetic"},
])
def test_invalid_inputs_fail_closed(case: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        evaluate(case)
