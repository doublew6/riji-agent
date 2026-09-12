"""Synthetic worker-level failure diagnostics without retaining rejected output."""

from __future__ import annotations

import json

import pytest

from riji_agent.memory.organization_store import OrganizationStore
from riji_agent.models.types import AssistantTurn, LLMError
from test_journal_initialization_budget import budget
from test_journal_organization import ObservationModel
from test_journal_organization_recovery import setup_organization


CANARY = "synthetic-private-model-output-should-never-be-retained"


class InvalidReport(ObservationModel):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure

    def complete(self, messages, tools):
        result = json.loads(super().complete(messages, tools).content)
        topic = result["topics"][0]
        mid = topic["evidence_ids"][0]
        if self.failure == "invalid_json":
            return AssistantTurn("{" + CANARY)
        if self.failure == "invalid_report":
            result["topics"] = None
        elif self.failure == "incomplete_report":
            result["topics"] = []
        elif self.failure == "invalid_entry":
            result["topics"] = [CANARY]
        elif self.failure == "invalid_evidence":
            topic["evidence_ids"] = [CANARY]
        elif self.failure == "invalid_category":
            topic["category"] = CANARY
        elif self.failure == "invalid_comparison":
            result["comparisons"] = [{"evidence_ids": [mid], "kind": "related", "reason": CANARY}]
        elif self.failure == "invalid_text":
            topic["title"] = CANARY * 2
        elif self.failure == "invalid_observations":
            result["observations"] = CANARY
        elif self.failure == "invalid_observation_evidence":
            result["observations"] = [{"summary": CANARY, "support_ids": [CANARY],
                                       "counter_ids": [], "limitation": "Needs more evidence."}]
        elif self.failure == "insufficient_independent_evidence":
            result["observations"] = [{"summary": CANARY, "support_ids": [mid],
                                       "counter_ids": [], "limitation": "Only one supporting event."}]
        return AssistantTurn(json.dumps(result))


@pytest.mark.parametrize("failure", [
    "invalid_json", "invalid_report", "incomplete_report", "invalid_entry", "invalid_evidence",
    "invalid_category", "invalid_comparison", "invalid_text", "invalid_observations",
    "invalid_observation_evidence", "insufficient_independent_evidence",
])
def test_rejected_report_keeps_exact_category_but_not_model_text(tmp_path, caplog, capsys, failure):
    engine, store, model, worker, _ = setup_organization(
        tmp_path, count=1, model=InvalidReport(failure))
    before = budget(engine, "initialization:")
    run_id = store.request("u1")
    assert worker.process_next()
    failed = dict(store._conn.execute(
        "SELECT * FROM memory_organization_runs WHERE id=?", (run_id,)).fetchone())
    assert failed["status"] == "failed"
    assert failed["error_code"] == "organization_" + failure
    assert failed["report_json"] is None
    assert engine.initialization_status()["organization"] == {"failed": 1}
    assert len(model.batches) == 1 and budget(engine, "initialization:") > before
    assert budget(engine, "day:") == 0
    # Continuation may settle the batch, but cannot retry this failed source version.
    for _ in range(3):
        if not worker.process_next():
            break
    assert len(model.batches) == 1
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err + caplog.text
    assert CANARY not in "\n".join(store._conn.iterdump())
    assert CANARY not in "\n".join(engine.store._conn.iterdump())


@pytest.mark.parametrize("code", [
    "codex_home_not_isolated", "codex_home_permissions_invalid", "codex_unsupported_version",
    "codex_timeout", "codex_unavailable", "codex_queue_timeout", "codex_invalid_response",
    "codex_request_too_large", "codex_request_failed", "codex_response_too_large",
    "codex_invalid_protocol", "codex_unexpected_tool_activity",
])
def test_known_provider_failure_is_classified_without_consuming_unsent_seed(tmp_path, code):
    class ProviderFailure(ObservationModel):
        def complete_with_guard(self, messages, tools, *, before_send):
            raise LLMError(code)

    engine, store, model, worker, _ = setup_organization(
        tmp_path, count=1, model=ProviderFailure())
    before = budget(engine, "initialization:")
    store.request("u1")
    assert worker.process_next() and not worker.process_next()
    assert store.latest("u1")["error_code"] == code
    assert store.latest("u1")["status"] == "failed"
    assert engine.initialization_status()["organization"] == {"pending": 1}
    assert not model.batches and budget(engine, "initialization:") == before


@pytest.mark.parametrize("error", [
    ValueError("invalid_evidence " + CANARY),
    ValueError("invalid_organization", CANARY),
    ValueError(CANARY),
    LLMError("codex_timeout " + CANARY),
    LLMError("codex_future_error " + CANARY),
    RuntimeError("invalid_organization"),
])
def test_unknown_or_near_match_exception_is_generic_and_redacted(tmp_path, caplog, capsys, error):
    class UnknownFailure(ObservationModel):
        def complete(self, messages, tools):
            raise error

    engine, store, _, worker, _ = setup_organization(tmp_path, count=1, model=UnknownFailure())
    run_id = store.request("u1")
    assert worker.process_next()
    assert store._conn.execute(
        "SELECT error_code FROM memory_organization_runs WHERE id=?", (run_id,)).fetchone()[0] == "organization_failed"
    assert CANARY not in "\n".join(store._conn.iterdump())
    assert CANARY not in "\n".join(engine.store._conn.iterdump())
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("code", ["codex_quota_exhausted", "codex_login_required"])
def test_retryable_provider_failure_keeps_existing_deferred_behavior(tmp_path, code):
    class DeferredFailure(ObservationModel):
        def complete_with_guard(self, messages, tools, *, before_send):
            raise LLMError(code)

    engine, store, _, worker, _ = setup_organization(tmp_path, count=1, model=DeferredFailure())
    before = budget(engine, "initialization:")
    store.request("u1")
    assert worker.process_next() and not worker.process_next()
    latest = store.latest("u1")
    assert latest["status"] == "pending" and latest["error_code"] == code
    assert latest["available_at"] > latest["updated_at"]
    assert engine.initialization_status()["organization"] == {"pending": 1}
    assert budget(engine, "initialization:") == before


@pytest.mark.parametrize("unsafe_code", [None, CANARY, "codex_timeout " + CANARY, [CANARY], {"raw": CANARY}])
def test_store_boundary_rejects_arbitrary_diagnostic_values(tmp_path, unsafe_code):
    store = OrganizationStore(tmp_path / "organization.sqlite3")
    run_id = store.request("synthetic-user")
    store.finish(run_id, None, error_code=unsafe_code)
    assert store.latest("synthetic-user")["error_code"] == "organization_failed"
    assert CANARY not in "\n".join(store._conn.iterdump())
    store.finish(run_id, {"groups": []}, error_code="codex_timeout")
    latest = store.latest("synthetic-user")
    assert latest["status"] == "ready" and latest["error_code"] is None
    assert latest["report"] == {"groups": []}
