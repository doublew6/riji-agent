"""Synthetic persistence and selection rules for trusted journal checkpoints."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from riji_agent.memory.organization_store import OrganizationStore


@pytest.fixture
def store(tmp_path: Path):
    database = OrganizationStore(tmp_path / "organization.sqlite3")
    yield database
    database.close()


def report() -> dict[str, Any]:
    return {
        "fingerprint": "synthetic-fingerprint", "total": 2, "processed": 1, "remaining": 1,
        "groups": [{
            "topics": [{"category": "other", "title": "Synthetic theme",
                        "summary": "A validated synthetic summary.", "evidence_ids": ["memory-one"]}],
            "comparisons": [], "time_bound_ids": [], "observations": [],
            "scope": "shared", "persona_id": None, "versions": {"memory-one": "version-one"},
        }],
        "covered_seeds": {"memory-one": "version-one"}, "stopped_seeds": {},
        "versions": {"memory-one": "version-one"}, "catalog_epoch": "synthetic-epoch",
        "review_after_days": 90,
    }


def processing(store: OrganizationStore, user_id: str = "synthetic-owner") -> int:
    run_id = store.request(user_id)
    assert store.claim()["id"] == run_id
    return run_id


def inject(store: OrganizationStore, run_id: int, payload: Any, status: str = "processing") -> None:
    with store._conn:
        store._conn.execute("UPDATE memory_organization_runs SET report_json=?,status=? WHERE id=?",
                            (json.dumps(payload), status, run_id))


def test_checkpoint_updates_only_progress_and_timestamp_without_mutating_input(store):
    run_id = processing(store)
    payload = report()
    original = deepcopy(payload)
    with store._conn:
        store._conn.execute("UPDATE memory_organization_runs SET updated_at=?,error_code=?,available_at=? WHERE id=?",
                            ("2000-01-01", "synthetic-diagnostic", "synthetic-availability", run_id))
    before = store.latest("synthetic-owner")
    store.record_checkpoint(run_id, payload)
    after = store.latest("synthetic-owner")
    assert payload == original and "journal_checkpoint" not in payload
    assert after["report"] == dict(original, journal_checkpoint=1)
    assert type(after["report"]["journal_checkpoint"]) is int
    assert after["updated_at"] != before["updated_at"]
    assert {key: value for key, value in after.items() if key not in {"report", "updated_at"}} == {
        key: value for key, value in before.items() if key not in {"report", "updated_at"}}
    assert store.latest_report("synthetic-owner") == after
    assert store.latest("synthetic-owner", ready_only=True) is None


def test_checkpoint_is_committed_and_reusable_after_reopening(tmp_path):
    path = tmp_path / "organization.sqlite3"
    first = OrganizationStore(path)
    run_id = processing(first)
    first.record_checkpoint(run_id, report())
    first.finish(run_id, None, error_code="codex_invalid_response")
    first.close()
    reopened = OrganizationStore(path)
    try:
        selected = reopened.latest_report("synthetic-owner")
        assert selected["status"] == "failed" and selected["error_code"] == "codex_invalid_response"
        assert selected["report"] == dict(report(), journal_checkpoint=1)
    finally:
        reopened.close()


@pytest.mark.parametrize("status", ["pending", "failed", "ready"])
def test_checkpoint_cannot_modify_a_run_after_processing_ends(store, status):
    run_id = processing(store)
    store.record_checkpoint(run_id, report())
    if status == "pending":
        store.defer(run_id, 3600, "journal_daily_budget")
    else:
        store.finish(run_id, report() if status == "ready" else None)
    before = store.latest("synthetic-owner")
    store.record_checkpoint(run_id, dict(report(), fingerprint="replacement-fingerprint"))
    store.record_checkpoint(run_id + 1000, report())
    assert store.latest("synthetic-owner") == before


@pytest.mark.parametrize("status", ["processing", "pending", "failed"])
def test_latest_report_accepts_checkpoint_without_claiming_ready(store, status):
    ready_id = processing(store)
    legacy = {"groups": [], "versions": {}, "fingerprint": "old-ready"}
    store.finish(ready_id, legacy)
    current_id = processing(store)
    store.record_checkpoint(current_id, report())
    if status == "pending":
        store.defer(current_id, 3600, "journal_daily_budget")
    elif status == "failed":
        store.finish(current_id, None, error_code="codex_invalid_response")
    selected = store.latest_report("synthetic-owner")
    assert selected["id"] == current_id and selected["status"] == status
    assert selected["report"] == dict(report(), journal_checkpoint=1)
    assert store.latest("synthetic-owner", ready_only=True)["id"] == ready_id
    assert store.latest("synthetic-owner", ready_only=True)["report"] == legacy


def test_latest_report_accepts_empty_presend_checkpoint_and_newer_ready(store):
    run_id = processing(store)
    base = dict(report(), groups=[], versions={}, covered_seeds={}, processed=0, remaining=2,
                catalog_epoch=None)
    store.record_checkpoint(run_id, base)
    assert store.latest_report("synthetic-owner")["report"] == dict(base, journal_checkpoint=1)
    store.finish(run_id, None)
    newer_id = processing(store)
    ready = {"groups": [], "versions": {}, "fingerprint": "new-ready"}
    store.finish(newer_id, ready)
    assert store.latest_report("synthetic-owner")["report"] == ready


@pytest.mark.parametrize("manifest", [
    {"input_batches": [[{"id": "memory-one", "version": "version-one"}]]},
    dict(report(), input_batches=[]),
])
def test_legacy_inputs_preserve_record_input_and_ready_fallback(store, manifest):
    ready_id = processing(store)
    store.finish(ready_id, {"groups": [], "versions": {}, "fingerprint": "old-ready"})
    current_id = processing(store)
    store.record_input(current_id, manifest)
    assert store.latest("synthetic-owner")["report"] == manifest
    assert store.latest_report("synthetic-owner")["id"] == ready_id
    store.finish(current_id, None)
    assert store.latest("synthetic-owner")["report"] == manifest
    assert store.latest_report("synthetic-owner")["id"] == ready_id


def invalid_payloads():
    values = [None, [], {}, {"input_batches": [], "journal_checkpoint": 1}]
    for marker in (None, True, False, "1", 1.0, 0, 2):
        values.append(dict(report(), journal_checkpoint=marker))
    for field in report():
        missing = dict(report(), journal_checkpoint=1)
        del missing[field]
        values.append(missing)
    for field, value in (("total", True), ("processed", -1), ("remaining", "1"),
                         ("review_after_days", None), ("fingerprint", ""), ("catalog_epoch", {}),
                         ("covered_seeds", {"memory-one": None}), ("stopped_seeds", []),
                         ("versions", {"": "version-one"}), ("groups", {}), ("raw_output", "CANARY")):
        values.append(dict(report(), journal_checkpoint=1, **{field: value}))
    return values


@pytest.mark.parametrize("payload", invalid_payloads())
def test_malformed_or_unversioned_progress_is_skipped_with_ready_fallback(store, payload):
    ready_id = processing(store)
    store.finish(ready_id, {"groups": [], "versions": {}, "fingerprint": "old-ready"})
    current_id = processing(store)
    inject(store, current_id, payload)
    assert store.latest_report("synthetic-owner")["id"] == ready_id
    store.clear_user("synthetic-owner")
    current_id = processing(store)
    inject(store, current_id, payload)
    assert store.latest_report("synthetic-owner") is None


def malformed_groups():
    groups = [None, {}, dict(report()["groups"][0], versions={})]
    for field, value in (("topics", []), ("comparisons", {}), ("time_bound_ids", ["other-memory"]),
                         ("observations", {}), ("scope", "unknown"), ("persona_id", []),
                         ("raw_output", "CANARY")):
        groups.append(dict(report()["groups"][0], **{field: value}))
    for field in report()["groups"][0]:
        missing = deepcopy(report()["groups"][0])
        del missing[field]
        groups.append(missing)
    topic = report()["groups"][0]["topics"][0]
    for field, value in (("category", "unknown"), ("title", []), ("summary", ""),
                         ("evidence_ids", ["other-memory"]), ("raw_output", "CANARY")):
        groups.append(dict(report()["groups"][0], topics=[dict(topic, **{field: value})]))
    return groups


@pytest.mark.parametrize("group", malformed_groups())
def test_malformed_group_cannot_be_written_or_selected(store, group):
    run_id = processing(store)
    payload = dict(report(), groups=[group])
    with pytest.raises(ValueError, match="^invalid_journal_checkpoint$"):
        store.record_checkpoint(run_id, payload)
    assert store.latest("synthetic-owner")["report"] is None
    inject(store, run_id, dict(payload, journal_checkpoint=1))
    assert store.latest_report("synthetic-owner") is None


@pytest.mark.parametrize("payload", [None, [], {}, dict(report(), journal_checkpoint=1),
                                     dict(report(), total=True), dict(report(), raw_output="CANARY")])
def test_invalid_checkpoint_input_is_rejected_without_overwriting_progress(store, payload):
    run_id = processing(store)
    store.record_checkpoint(run_id, report())
    before = store.latest("synthetic-owner")
    with pytest.raises(ValueError, match="^invalid_journal_checkpoint$"):
        store.record_checkpoint(run_id, payload)
    assert store.latest("synthetic-owner") == before


def observation_report() -> dict[str, Any]:
    payload = report()
    versions = {f"memory-{index}": f"version-{index}" for index in range(3)}
    group = payload["groups"][0]
    group["versions"] = versions
    group["topics"][0]["evidence_ids"] = list(versions)
    group["observations"] = [{
        "summary": "A tentative synthetic observation.", "support_ids": ["memory-0", "memory-1"],
        "counter_ids": ["memory-2"], "limitation": "Limited to synthetic evidence.",
    }]
    payload.update(total=3, versions=versions, covered_seeds={"memory-0": "version-0"})
    return payload


def test_checkpoint_accepts_complete_observation_shape_without_source_date_validation(store):
    run_id = processing(store)
    payload = observation_report()
    payload["groups"][0].update(scope="persona", persona_id="synthetic-mentor")
    store.record_checkpoint(run_id, payload)
    assert store.latest_report("synthetic-owner")["report"] == dict(payload, journal_checkpoint=1)


def test_checkpoint_preserves_whitespace_text_accepted_by_journal_observation_validator(store):
    run_id = processing(store)
    payload = observation_report()
    payload["groups"][0]["observations"][0].update(summary=" ", limitation="\t")
    store.record_checkpoint(run_id, payload)
    assert store.latest_report("synthetic-owner")["report"] == dict(payload, journal_checkpoint=1)


@pytest.mark.parametrize("field,value", [
    ("summary", []), ("summary", ""), ("summary", "x" * 241), ("summary", "password CANARY"),
    ("limitation", ""), ("limitation", "x" * 181), ("support_ids", "memory-0"),
    ("support_ids", ["memory-0"]), ("support_ids", ["memory-0", "memory-0"]),
    ("support_ids", ["memory-0", "unknown-memory"]), ("counter_ids", ["memory-0"]),
    ("counter_ids", ["memory-2", "memory-2"]), ("counter_ids", ["unknown-memory"]),
    ("counter_ids", [[]]), ("raw_output", "CANARY"),
])
def test_checkpoint_rejects_malformed_observations_and_reference_links(store, field, value):
    run_id = processing(store)
    payload = observation_report()
    payload["groups"][0]["observations"][0][field] = value
    with pytest.raises(ValueError, match="^invalid_journal_checkpoint$"):
        store.record_checkpoint(run_id, payload)
    inject(store, run_id, dict(payload, journal_checkpoint=1))
    assert store.latest_report("synthetic-owner") is None


def test_latest_report_ignores_malformed_json_and_isolates_users(store):
    first_id = processing(store)
    store.record_checkpoint(first_id, report())
    store.finish(first_id, None)
    other_id = processing(store, "another-owner")
    store.finish(other_id, {"groups": [], "fingerprint": "another-owner-ready"})
    corrupt_id = processing(store)
    with store._conn:
        store._conn.execute("UPDATE memory_organization_runs SET report_json=? WHERE id=?", ("{invalid", corrupt_id))
    assert store.latest_report("synthetic-owner")["id"] == first_id
    assert store.latest_report("another-owner")["id"] == other_id
    assert store.latest_report("missing-owner") is None
