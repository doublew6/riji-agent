"""Synthetic schema constraints complement, but never replace, evidence validation."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from riji_agent.memory.journal_organization import _organization_schema
from riji_agent.memory.models import MemoryScope
from riji_agent.models.types import AssistantTurn
from test_journal_organization import ObservationModel
from test_journal_organization_recovery import setup_organization


DAY_ONE = "2026-01-01"
DAY_TWO = "2026-01-02"
DAY_CASES = [
    ({"a": [], "b": []}, False, ["a", "b"]),
    ({"a": [DAY_ONE], "b": []}, False, ["a"]),
    ({"a": [DAY_ONE], "b": [DAY_ONE]}, False, ["a", "b"]),
    ({"a": [DAY_ONE, DAY_TWO], "b": []}, False, ["a"]),
    ({"a": [DAY_ONE], "b": [DAY_TWO]}, True, ["a", "b"]),
]


def observation(support_ids):
    return {"summary": "Tentative synthetic observation.", "support_ids": support_ids,
            "counter_ids": [], "limitation": "Only a few synthetic events support this."}


def response(ids, observations=None):
    return {"topics": [{"category": "other", "title": "Synthetic records",
                       "summary": "Keep the separate input records.", "evidence_ids": ids}],
            "comparisons": [], "time_bound_ids": [], "observations": observations or []}


@pytest.mark.parametrize("days,enabled,supports", DAY_CASES)
def test_schema_requires_two_eligible_records_and_two_distinct_days(days, enabled, supports):
    schema = _organization_schema(["a", "b"], days)
    observations = schema["properties"]["observations"]
    assert observations["maxItems"] == (3 if enabled else 0)
    properties = observations["items"]["properties"]
    assert properties["support_ids"]["items"]["enum"] == supports
    assert properties["counter_ids"]["items"]["enum"] == ["a", "b"]
    assert '"enum": []' not in json.dumps(schema)


@pytest.mark.parametrize("days,enabled,supports", DAY_CASES)
def test_dynamic_schema_is_valid_json_schema_and_rejects_impossible_observations(days, enabled, supports):
    jsonschema = pytest.importorskip("jsonschema")
    schema = _organization_schema(["a", "b"], days)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(response(["a", "b"]))
    assert validator.is_valid(response(["a", "b"], [observation(["a", "b"])])) is enabled


def test_schema_filters_support_ids_without_excluding_topics_or_counterexamples():
    ids = ["event-one", "event-two", "plan", "native"]
    schema = _organization_schema(ids, {"event-one": [DAY_ONE], "event-two": [DAY_TWO],
                                        "plan": [], "native": [], "outside-batch": [DAY_ONE]})
    properties = schema["properties"]
    assert properties["topics"]["items"]["properties"]["evidence_ids"]["items"]["enum"] == ids
    assert properties["observations"]["maxItems"] == 3
    evidence = properties["observations"]["items"]["properties"]
    assert evidence["support_ids"]["items"]["enum"] == ["event-one", "event-two"]
    assert evidence["counter_ids"]["items"]["enum"] == ids


def test_no_evidence_metadata_disables_observations_without_an_empty_enum():
    schema = _organization_schema(["only-record"])
    assert schema["properties"]["observations"]["maxItems"] == 0
    assert '"enum": []' not in json.dumps(schema)


class StructuredReport(ObservationModel):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.schemas = []

    def complete_json_with_guard(self, messages, schema, *, before_send):
        self.schemas.append(schema)
        before_send()
        payload = json.loads(super().complete(messages, []).content)
        if self.failure == "missing_observations":
            del payload["observations"]
        elif self.failure == "non_object":
            payload = []
        elif self.failure == "unsupported_observation":
            ids = payload["topics"][0]["evidence_ids"]
            payload["observations"] = [observation(ids)]
        return AssistantTurn(json.dumps(payload))


def test_goal_only_batch_still_finishes_complete_topic_organization(tmp_path):
    model = StructuredReport()
    engine, store, _, worker, _ = setup_organization(tmp_path, count=2, model=model)
    run_id = store.request("u1")
    assert worker.process_next()
    run = dict(store._conn.execute("SELECT * FROM memory_organization_runs WHERE id=?", (run_id,)).fetchone())
    assert run["status"] == "ready" and run["error_code"] is None
    assert engine.initialization_status()["organization"] == {"done": 2}
    assert all(schema["properties"]["observations"]["maxItems"] == 0 for schema in model.schemas)
    assert all(not item["independent_days"] for batch in model.batches for item in batch)
    assert all(not group["observations"] for group in json.loads(run["report_json"])["groups"])


def test_call_derives_schema_supports_from_real_payload_evidence_rules(tmp_path):
    model = StructuredReport()
    engine, _, _, _, journal = setup_organization(tmp_path, count=4, model=model, daily_chars=100000)
    records = list(engine.backend.records.values())
    for index, item in enumerate(records):
        metadata = dict(item.metadata)
        metadata["journal_kind"] = "event" if index != 2 else "goal"
        metadata["certainty"] = "inferred" if index == 3 else "explicit"
        engine.backend.records[item.id] = replace(item, metadata=metadata)
    native = engine.backend.add("A synthetic native memory.", user_id="u1", scope=MemoryScope.SHARED,
                                persona_id=None, metadata={"privacy": "cloud"})[0]
    batch = list(engine.backend.records.values())
    parsed = journal._call(batch)
    days = {item["id"]: item["independent_days"] for item in model.batches[0]}
    assert all(days[item.id] for item in records[:2])
    assert all(not days[item.id] for item in (*records[2:], native))
    observations = model.schemas[0]["properties"]["observations"]
    assert observations["maxItems"] == 3
    assert observations["items"]["properties"]["support_ids"]["items"]["enum"] == [item.id for item in records[:2]]
    assert set(parsed["topics"][0]["evidence_ids"]) == {item.id for item in batch}


@pytest.mark.parametrize("failure,error", [
    ("missing_observations", "organization_invalid_observations"),
    ("non_object", "organization_invalid_report"),
    ("unsupported_observation", "organization_insufficient_independent_evidence"),
])
def test_provider_ignoring_schema_still_fails_without_retaining_partial_topics(tmp_path, failure, error):
    model = StructuredReport(failure)
    engine, store, _, worker, _ = setup_organization(tmp_path, count=2, model=model)
    run_id = store.request("u1")
    assert worker.process_next()
    run = dict(store._conn.execute("SELECT * FROM memory_organization_runs WHERE id=?", (run_id,)).fetchone())
    assert run["status"] == "failed" and run["error_code"] == error
    assert run["report_json"] is None
    assert store.latest("u1", ready_only=True) is None
    assert engine.initialization_status()["organization"] == {"failed": 1, "pending": 1}
    assert len(model.schemas) == 1 and len(model.batches) == 1
    assert model.schemas[0]["properties"]["observations"]["maxItems"] == 0


def test_two_different_days_still_do_not_permit_repeated_support_or_counter_overlap(tmp_path):
    engine, _, _, _, journal = setup_organization(tmp_path, count=2)
    records = list(engine.backend.records.values())
    records = [replace(item, metadata=dict(item.metadata, journal_kind="event")) for item in records]
    assert all(journal._days(item) for item in records)
    ids = [item.id for item in records]
    assert len(set.union(*(journal._days(item) for item in records))) == 2
    with pytest.raises(ValueError, match="^invalid_observation_evidence$"):
        journal._validate_observations([observation([ids[0], ids[0]])], records)
    overlapping = dict(observation(ids), counter_ids=[ids[0]])
    with pytest.raises(ValueError, match="^insufficient_independent_evidence$"):
        journal._validate_observations([overlapping], records)
    assert journal._validate_observations([observation(ids)], records) == [observation(ids)]
