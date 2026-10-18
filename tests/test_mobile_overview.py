"""Synthetic projection, source lifecycle and device/action admission coverage."""

import copy
import json
from dataclasses import replace

import pytest
import httpx
from fastapi.testclient import TestClient

from riji_agent.memory.models import MemoryScope, MemoryStatus
from riji_agent.memory.organization import MemoryOrganizer, memory_fingerprint, memory_version
from riji_agent.memory.serialized import SerializedMemoryBackend
from riji_agent.mobile.memory import MobileMemory
from riji_agent.mobile.overview import MobileOverview, THEMES
from riji_agent.mobile.proxy import admitted
from riji_agent.mobile.public_proxy import create_public_proxy_app
from test_mobile_api import mobile, record, post
from test_mentor_runtime import runtime as mentor_runtime
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note
from test_journal_organization import EventExtractor, ObservationModel


def save_report(service, groups, *, journal=False):
    store = service.operations.organization
    ids = {mid for value in groups if isinstance(value, dict) for topic in value.get("topics", []) for mid in topic["evidence_ids"]}
    versions = {item.id: memory_version(item) for item in service.list_memories(user_id="synthetic-owner") if item.id in ids}
    report = {"groups": groups, "versions": versions, "total": len(versions), "processed": len(versions)}
    if journal:
        report.update(catalog_epoch=service.journal.store.get_control("catalog_epoch"), covered_seeds=versions)
    run = store.request("synthetic-owner")
    store.finish(run, report)
    return report


def group(ids=("memory-one",), *, scope="shared", persona=None):
    return {"topics": [{"category": "preferences", "title": "Synthetic theme", "summary": "A bounded synthetic summary",
                        "evidence_ids": list(ids)}], "comparisons": [], "time_bound_ids": [],
            "scope": scope, "persona_id": persona}


def overview(client, cursor=None):
    return client.get("/mobile/v1/memory-overview", params={"cursor": cursor} if cursor else {})


def test_legacy_projection_capability_read_only_and_stable_version(mobile):
    service, client, provider, _ = mobile
    report = save_report(service.memory.service, [group()])
    report["private_metadata"] = "SENSITIVE_REPORT_FIELD"
    assert client.get("/mobile/v1/capabilities").json()["capabilities"]["memory_overview"]
    page = overview(client).json()
    assert page["state"] == "ready" and page["coverage"]["organized"] == 1
    assert [item["id"] for item in page["categories"]] == list(THEMES)
    assert [item["id"] for item in page["evidence"]] == ["memory-one"]
    assert "metadata" not in page["evidence"][0] and "report" not in page
    assert "another-owner" not in json.dumps(page) and "SENSITIVE" not in json.dumps(page)
    before = service.memory.service.operations.organization.latest("synthetic-owner")
    assert overview(client).json()["version"] == page["version"]
    assert service.memory.service.operations.organization.latest("synthetic-owner") == before and not provider.calls


@pytest.mark.parametrize("change", ["text", "archive", "delete", "permission", "scope", "persona", "foreign", "source"])
def test_entire_group_hidden_when_any_evidence_changes(mobile, change):
    service, client, _, _ = mobile
    backend = service.memory.service.backend.backend
    backend.records["memory-two"] = record("memory-two")
    save_report(service.memory.service, [group(("memory-one", "memory-two"))])
    assert overview(client).json()["summaries"]
    item = backend.records["memory-two"]
    if change == "delete": del backend.records[item.id]
    elif change == "text": backend.records[item.id] = replace(item, content="Changed fact")
    elif change == "archive": backend.records[item.id] = replace(item, status=MemoryStatus.ARCHIVED)
    elif change == "permission": backend.records[item.id] = replace(item, metadata={"privacy": "none"})
    elif change == "scope": backend.records[item.id] = replace(item, scope=MemoryScope.PERSONA)
    elif change == "persona": backend.records[item.id] = replace(item, persona_id="future_self")
    elif change == "foreign": backend.records[item.id] = replace(item, user_id="another-owner")
    else: backend.records[item.id] = replace(item, metadata={"journal_state": "source_invalid"})
    page = overview(client).json()
    assert not page["summaries"] and not page["evidence"]


@pytest.mark.parametrize("damage", ["missing", "duplicate", "foreign", "secret", "bad_scope", "extra_ref", "malformed"])
def test_stored_report_is_untrusted(mobile, damage):
    service, client, _, _ = mobile
    payload = group()
    if damage == "missing": payload["topics"] = []
    elif damage == "duplicate": payload["topics"][0]["evidence_ids"] *= 2
    elif damage == "foreign": payload["topics"][0]["evidence_ids"] = ["other-memory"]
    elif damage == "secret": payload["topics"][0]["summary"] = "api_key=" + "sk-" + "a" * 40
    elif damage == "bad_scope": payload["scope"] = "persona"
    elif damage == "extra_ref": payload["comparisons"] = [{"kind": "conflict", "reason": "Synthetic", "evidence_ids": ["memory-one", "other-memory"]}]
    else: payload = "not a group"
    save_report(service.memory.service, [payload])
    page = overview(client).json()
    assert not page["summaries"] and not page["evidence"]


def test_persona_themes_are_separate_and_comparisons_keep_both_refs(mobile):
    service, client, _, _ = mobile
    raw = service.memory.service.backend.backend
    raw.records["p1"] = replace(record("p1"), scope=MemoryScope.PERSONA, persona_id="future_self")
    raw.records["p2"] = replace(record("p2"), scope=MemoryScope.PERSONA, persona_id="future_self")
    private = group(("p1", "p2"), scope="persona", persona="future_self")
    private["comparisons"] = [{"kind": "conflict", "reason": "Keep both distinct sources", "evidence_ids": ["p1", "p2"]}]
    save_report(service.memory.service, [group(), private])
    page = overview(client).json()
    assert page["categories"][0]["summary_count"] == 1
    assert page["summaries"][1]["states"] == ["current", "observation"]
    assert page["comparisons"][0]["persona_id"] == "future_self"
    assert set(page["comparisons"][0]["evidence_ids"]) == {"p1", "p2"}


def test_versioned_pagination_includes_only_referenced_evidence(mobile):
    service, client, _, _ = mobile
    raw = service.memory.service.backend.backend
    groups = []
    for number in range(45):
        identifier = f"m{number}"
        raw.records[identifier] = record(identifier)
        groups.append(group((identifier,)))
    save_report(service.memory.service, groups)
    first = overview(client).json()
    assert len(first["summaries"]) == len(first["evidence"]) == 20 and first["next_cursor"]
    second = overview(client, first["next_cursor"]).json()
    assert first["version"] == second["version"]
    assert set(item["id"] for item in first["evidence"]).isdisjoint(item["id"] for item in second["evidence"])
    raw.records["m44"] = replace(raw.records["m44"], metadata={"privacy": "local"})
    response = overview(client, first["next_cursor"])
    assert response.status_code == 409 and response.json()["detail"] == "memory_overview_changed"


def test_report_and_latest_job_change_version_without_changing_memories(mobile):
    service, client, _, _ = mobile
    save_report(service.memory.service, [group()])
    before = overview(client).json()["version"]
    modified = group(); modified["topics"][0]["summary"] = "Different bounded summary"
    save_report(service.memory.service, [modified])
    assert overview(client).json()["version"] != before
    before = overview(client).json()["version"]
    service.memory.service.operations.organization.request("synthetic-owner")
    assert overview(client).json()["version"] != before


def test_organize_receipt_replay_and_backoff_preserved(mobile):
    service, client, provider, _ = mobile
    page = overview(client).json()
    assert page["state"] == "unorganized" and page["can_organize"]
    body = {"operation_id": "organize-once", "action": "organize", "expected_version": page["version"],
            "binding": page["organize_binding"], "acknowledged": True}
    first = post(client, "memory-overview/actions", **body)
    assert first["kind"] == "memory_organize" and first["result"]["state"] == "pending"
    assert post(client, "memory-overview/actions", **body) == first
    store = service.memory.service.operations.organization
    run = store.claim(); store.defer(run["id"], 3600, "journal_daily_budget")
    before = store.latest("synthetic-owner")
    page = overview(client).json()
    assert not page["can_organize"] and page["organize_reason"] == "already_pending"
    body.update(operation_id="organize-second", expected_version=page["version"])
    assert post(client, "memory-overview/actions", **body)["result"]["state"] == "already_pending"
    assert store.latest("synthetic-owner") == before and not provider.calls
    assert client.get("/mobile/v1/operations/organize-once").json() == first


def test_snapshot_rechecks_before_return(mobile, monkeypatch):
    service, client, _, _ = mobile
    save_report(service.memory.service, [group()])
    original = service.overview._project
    def changed(*args):
        result = original(*args)
        raw = service.memory.service.backend.backend
        raw.records["memory-one"] = replace(raw.records["memory-one"], content="Changed outside cooperative lock")
        return result
    monkeypatch.setattr(service.overview, "_project", changed)
    assert overview(client).status_code == 409


def test_empty_failed_and_local_permission_are_distinct(mobile):
    service, client, _, _ = mobile
    raw = service.memory.service.backend.backend
    raw.records["memory-one"] = replace(raw.records["memory-one"], metadata={"privacy": "local"})
    assert overview(client).json()["state"] == "permission_denied"
    raw.records.clear()
    assert overview(client).json()["state"] == "empty"
    run = service.memory.service.operations.organization.request("synthetic-owner")
    service.memory.service.operations.organization.finish(run, None)
    assert overview(client).json()["state"] == "failed"


@pytest.fixture
def journal_overview(tmp_path):
    engine, model = runtime(tmp_path, model=EventExtractor(), daily_chars=500000)
    paths = [write_note(engine.policy.root, f"daily/2026-01-{day:02}.md", text) for day, text in (
        (1, "第一次深呼吸有效，缓解紧张。"), (2, "第二次深呼吸有效，放松下来。"), (3, "这次深呼吸失败，仍然紧张。"))]
    engine.scan()
    while engine.process_next(): pass
    service = attached_service(tmp_path, engine)
    service.backend = SerializedMemoryBackend(service.backend, tmp_path / "mutations.lock")
    organizer = MemoryOrganizer(service.backend, service.operations.organization, ObservationModel())
    service.operations.organization.request("u1")
    while organizer.process_next(): pass
    yield MobileOverview(MobileMemory(service)), engine, service, paths, model
    service.operations.close(); engine.store.close()


def test_journal_real_observations_counterevidence_and_current_source_revalidation(journal_overview):
    view, engine, service, paths, model = journal_overview
    page = view.listing("u1")
    assert page["state"] == "ready" and page["observations"]
    observation = page["observations"][0]
    assert len(observation["support_ids"]) == 2 and len(observation["counter_ids"]) == 1
    assert len({item["observed_at"] for item in page["evidence"] if item["id"] in observation["support_ids"]}) == 2
    calls = copy.deepcopy(model.calls)
    paths[0].unlink()
    changed = view.listing("u1")
    assert not changed["summaries"] and not changed["observations"] and changed["coverage"]["invalid"]
    assert model.calls == calls


def test_journal_observation_dates_not_report_assertions(journal_overview, monkeypatch):
    view, engine, service, _, _ = journal_overview
    assert view.listing("u1")["observations"]
    from riji_agent.memory.journal_organization import JournalOrganization
    monkeypatch.setattr(JournalOrganization, "_days", lambda self, item: {"2026-01-01"})
    assert not view.listing("u1")["observations"]


def save_checkpoint(service, report, status):
    store = service.operations.organization
    run = store.request("u1")
    assert store.claim()["id"] == run
    store.record_checkpoint(run, report)
    if status == "pending":
        store.defer(run, 3600, "journal_daily_budget")
    elif status == "failed":
        store.finish(run, None)
    return run


def partial_checkpoint_report(service):
    report = copy.deepcopy(service.operations.organization.latest("u1", ready_only=True)["report"])
    mid, version = next(iter(report["covered_seeds"].items()))
    report.update(covered_seeds={mid: version}, processed=1, remaining=report["total"] - 1)
    return report


@pytest.mark.parametrize("status", ["processing", "pending", "failed"])
def test_journal_checkpoint_projects_valid_partial_progress_and_real_job_status(journal_overview, status):
    view, engine, service, _, model = journal_overview
    report = partial_checkpoint_report(service)
    ready = service.operations.organization.latest("u1", ready_only=True)
    run = save_checkpoint(service, report, status)
    before = service.operations.organization.latest("u1")
    ledger = engine.store.rows("SELECT * FROM initialization_seeds")
    calls = copy.deepcopy(model.calls)
    page = view.listing("u1")
    assert page["job_status"] == status
    assert page["state"] == ("failed" if status == "failed" else "partial")
    assert page["coverage"]["organized"] == 1 and page["coverage"]["unorganized"] > 0
    assert page["summaries"] and page["observations"] and page["evidence"]
    assert page["organized_at"] == before["updated_at"]
    assert service.operations.organization.latest_report("u1")["id"] == run
    assert service.operations.organization.latest("u1", ready_only=True) == ready
    assert service.operations.organization.latest("u1") == before
    assert engine.store.rows("SELECT * FROM initialization_seeds") == ledger and model.calls == calls


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_journal_checkpoint_full_pagination_is_stable_and_read_only(journal_overview, status):
    view, engine, service, _, model = journal_overview
    expected = {f"checkpoint-{number:02}" for number in range(45)}
    for mid in sorted(expected):
        engine.backend.records[mid] = record(mid, "u1")
    records = service.list_memories(user_id="u1")
    seeds = [item for item in records if item.metadata.get("journal_kind") != "observation"]
    versions = {item.id: memory_version(item) for item in records if item.id in expected}
    groups = [dict(group((mid,)), versions={mid: versions[mid]}, observations=[]) for mid in sorted(expected)]
    report = {"fingerprint": memory_fingerprint(seeds), "total": len(seeds), "processed": 45,
              "groups": groups, "covered_seeds": versions, "stopped_seeds": {}, "remaining": len(seeds) - 45,
              "versions": versions, "catalog_epoch": engine.store.get_control("catalog_epoch"), "review_after_days": 90}
    save_checkpoint(service, report, status)
    before = service.operations.organization.latest("u1")
    ledger = engine.store.rows("SELECT * FROM initialization_seeds")
    calls = copy.deepcopy(model.calls)
    pages, cursor = [], ""
    while True:
        page = view.listing("u1", cursor)
        pages.append(page)
        assert len(pages) <= 3
        if not page["next_cursor"]:
            break
        cursor = page["next_cursor"]
    assert [len(page["summaries"]) for page in pages] == [20, 20, 5]
    assert len({page["version"] for page in pages}) == 1
    assert all(page["job_status"] == status and page["coverage"]["organized"] == 45 for page in pages)
    assert all(page["coverage"]["unorganized"] == len(records) - 45 for page in pages)
    assert {item["id"] for page in pages for item in page["evidence"]} == expected
    assert len({item["id"] for page in pages for item in page["summaries"]}) == 45
    assert service.operations.organization.latest("u1") == before and model.calls == calls
    assert engine.store.rows("SELECT * FROM initialization_seeds") == ledger


@pytest.mark.parametrize("status", ["pending", "failed"])
@pytest.mark.parametrize("change", ["owner", "permission", "source", "epoch", "version"])
def test_journal_checkpoint_revalidates_current_evidence(journal_overview, status, change):
    view, engine, service, paths, _ = journal_overview
    report = partial_checkpoint_report(service)
    save_checkpoint(service, report, status)
    before = view.listing("u1")
    assert before["summaries"] and before["observations"]
    mid = next(iter(report["covered_seeds"]))
    item = engine.backend.records[mid]
    if change == "owner":
        engine.backend.records[mid] = replace(item, user_id="another-owner")
    elif change == "permission":
        engine.privacy.revoke()
    elif change == "source":
        paths[0].unlink()
    elif change == "epoch":
        engine.store.bump_epoch()
    else:
        engine.backend.records[mid] = replace(item, content="Changed synthetic memory")
    page = view.listing("u1")
    assert page["version"] != before["version"] and page["job_status"] == status
    assert not page["summaries"] and not page["observations"] and not page["evidence"]


@pytest.mark.parametrize("status", ["processing", "pending", "failed"])
@pytest.mark.parametrize("manifest", [True, False])
def test_legacy_inflight_input_keeps_compatible_ready_report(mobile, status, manifest):
    service, client, _, _ = mobile
    memory = service.memory.service
    report = save_report(memory, [group()])
    ready = memory.operations.organization.latest("synthetic-owner", ready_only=True)
    store = memory.operations.organization
    run = store.request("synthetic-owner")
    assert store.claim()["id"] == run
    payload = {"input_batches": [[{"id": mid, "version": version} for mid, version in report["versions"].items()]]}
    if not manifest:
        payload = copy.deepcopy(report)
        payload["groups"][0]["topics"][0]["summary"] = "Untrusted in-flight report"
    store.record_input(run, payload)
    if status == "pending":
        store.defer(run, 3600, "journal_daily_budget")
    elif status == "failed":
        store.finish(run, None)
    page = overview(client).json()
    assert page["job_status"] == status and page["coverage"]["organized"] == 1
    assert page["state"] == ("failed" if status == "failed" else "ready")
    assert page["summaries"][0]["summary"] == report["groups"][0]["topics"][0]["summary"]
    assert [item["id"] for item in page["evidence"]] == ["memory-one"]
    assert store.latest_report("synthetic-owner") == ready


@pytest.mark.parametrize("control", ["paused", "restore_in_progress", "revoke", "epoch"])
def test_journal_controls_invalidate_or_disable_action_without_reopening(journal_overview, control):
    view, engine, service, _, _ = journal_overview
    before = view.listing("u1")
    manifest = engine.store.rows("SELECT * FROM initialization_seeds")
    if control == "revoke": engine.privacy.revoke()
    elif control == "epoch": engine.store.bump_epoch()
    else: engine.store.set_control(control, "1")
    page = view.listing("u1")
    assert page["version"] != before["version"]
    if control != "paused": assert not page["summaries"]
    if control != "epoch": assert not page["can_organize"]
    assert engine.store.rows("SELECT * FROM initialization_seeds") == manifest


def test_mobile_overview_proxy_exact_admission():
    assert admitted("GET", b"/mobile/v1/memory-overview", b"cursor=abc_DEF")
    assert admitted("POST", b"/mobile/v1/memory-overview/actions", b"")
    for method, path, query in (("GET", b"/mobile/v1/memory-overview", b"owner=other"),
                                ("POST", b"/mobile/v1/memory-overview/actions", b"cursor=abc"),
                                ("GET", b"/mobile/v1/memory-overview/report", b"")):
        assert not admitted(method, path, query)


def test_legacy_manifest_cannot_drop_an_input_and_keep_a_summary(mobile):
    service, client, _, _ = mobile
    memory = service.memory.service
    report = save_report(memory, [group()])
    report["versions"]["omitted-input"] = "a" * 64
    memory.operations.organization.finish(memory.operations.organization.latest("synthetic-owner")["id"], report)
    assert overview(client).json()["summaries"] == []


@pytest.mark.parametrize("change", ["version", "binding", "acknowledged", "extra", "numeric_ack"])
def test_organization_rejects_stale_binding_and_unacknowledged_or_untrusted_body(mobile, change):
    service, client, _, _ = mobile
    page = overview(client).json()
    body = {"operation_id": "bad-request", "action": "organize", "expected_version": page["version"],
            "binding": page["organize_binding"], "acknowledged": True}
    if change == "version": body["expected_version"] = "f" * 64
    if change == "binding": body["binding"] = "f" * 64
    if change == "acknowledged": body["acknowledged"] = False
    if change == "numeric_ack": body["acknowledged"] = 1
    if change == "extra": body["owner"] = "another-owner"
    response = client.post("/mobile/v1/memory-overview/actions", json=body)
    assert response.status_code == 422 or response.json()["state"] == "failed"
    assert service.memory.service.operations.organization.latest("synthetic-owner") is None


def test_stopped_coverage_is_explicit_and_get_never_repairs_seed_ledger(journal_overview):
    view, engine, service, _, _ = journal_overview
    store = service.operations.organization
    ready = store.latest("u1", ready_only=True)
    report = copy.deepcopy(ready["report"])
    mid, version = next(iter(report["covered_seeds"].items()))
    report["covered_seeds"].pop(mid)
    report["stopped_seeds"][mid] = version
    store.finish(ready["id"], report)
    engine.store.execute("INSERT OR REPLACE INTO initialization_seeds(id,version,state) VALUES (?,?,'failed')", (mid, version))
    before = engine.store.rows("SELECT * FROM initialization_seeds")
    page = view.listing("u1")
    assert page["state"] == "partial" and page["coverage"]["stopped"] == 1
    assert page["coverage"]["unorganized"] == 0 and not page["can_organize"]
    assert engine.store.rows("SELECT * FROM initialization_seeds") == before


def test_missing_journal_engine_cannot_admit_managed_evidence_as_legacy(mobile):
    service, client, _, _ = mobile
    raw = service.memory.service.backend.backend
    raw.records["memory-one"] = replace(raw.records["memory-one"], metadata={"privacy": "cloud", "journal_managed": True})
    save_report(service.memory.service, [group()])
    page = overview(client).json()
    assert page["summaries"] == [] and page["evidence"] == [] and not page["can_organize"]


def test_public_proxy_forwards_projection_and_receipt_but_revocation_blocks_both(mobile):
    service, backend_client, _, paired = mobile
    proxy = TestClient(create_public_proxy_app(service.store.path, transport=httpx.ASGITransport(app=backend_client.app)))
    proxy.headers["Authorization"] = "Bearer " + paired["device_token"]
    response = overview(proxy)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    page = response.json()
    body = {"operation_id": "public-organize", "action": "organize", "expected_version": page["version"],
            "binding": page["organize_binding"], "acknowledged": True}
    assert post(proxy, "memory-overview/actions", **body)["state"] == "completed"
    service.store.revoke(paired["device_id"])
    assert overview(proxy).status_code == 401
    assert proxy.post("/mobile/v1/memory-overview/actions", json=body).status_code == 401
