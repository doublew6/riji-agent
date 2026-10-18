"""Synthetic regressions for coverage lost when a neighboring memory changes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from riji_agent.memory.models import LongTermMemory, MemoryScope
from riji_agent.memory.organization import MemoryOrganizer, active_records, memory_version
from riji_agent.memory.organization_store import OrganizationStore
from riji_agent.mobile.overview import MobileOverview, _safe_record
from riji_agent.models.types import AssistantTurn
from test_journal_initialization_budget import budget
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime
from test_journal_organization import ObservationModel
from test_journal_organization_recovery import FailingOrganization, drain, setup_organization


def group(records: Sequence[LongTermMemory]) -> dict[str, Any]:
    return {
        "topics": [{"category": "other", "title": "Synthetic theme",
                    "summary": "Keep the synthetic evidence together.",
                    "evidence_ids": [item.id for item in records]}],
        "comparisons": [], "time_bound_ids": [], "observations": [],
        "scope": records[0].scope.value, "persona_id": records[0].persona_id,
        "versions": {item.id: memory_version(item) for item in records},
    }


def save_report(store: Any, engine: Any, groups: list[dict[str, Any]],
                covered: Sequence[LongTermMemory]) -> None:
    run_id = store.request(engine.policy.user_id)
    assert store.claim()["id"] == run_id
    store.finish(run_id, {
        "groups": groups, "covered_seeds": {item.id: memory_version(item) for item in covered},
        "remaining": 0, "catalog_epoch": engine.store.get_control("catalog_epoch"),
    })


def setup_native(tmp_path: Path, count: int = 3) -> tuple[Any, ...]:
    engine, _ = runtime(tmp_path, daily_chars=500000)
    for index in range(count):
        engine.backend.add(f"Synthetic fact {index}.", user_id="u1", scope=MemoryScope.SHARED,
                           persona_id=None, metadata={"privacy": "cloud"})
    # A bounded singleton search makes each queued seed's progress observable.
    engine.backend.search = lambda *args, **kwargs: ()
    service = attached_service(tmp_path, engine)
    store, model = service.operations.organization, ObservationModel()
    worker = MemoryOrganizer(service.backend, store, model)
    return engine, store, model, worker


def mobile_coverage(engine: Any, store: Any) -> dict[str, int]:
    records = dict(engine.backend.records)
    report = store.latest_report("u1")["report"]
    stopped = {row["id"]: row["version"] for row in engine.store.rows(
        "SELECT id,version FROM initialization_seeds WHERE state IN ('failed','spent','retry_failed','retry_spent')")}
    snapshot = SimpleNamespace(
        records=records, eligible={item.id: item for item in active_records(tuple(records.values()), "u1")
                                   if _safe_record(item) and engine.can_send(item, "organization")},
        report=report, stopped=stopped, journal=True,
        epoch=engine.store.get_control("catalog_epoch"),
    )
    overview = object.__new__(MobileOverview)
    groups, invalid = overview._groups(snapshot, "u1")
    return overview._coverage(snapshot, groups, invalid)


@pytest.mark.parametrize("change", ["changed", "deleted"])
def test_invalidated_neighbor_rebuilds_orphan_and_preserves_valid_coverage(tmp_path: Path, change: str) -> None:
    engine, store, model, worker = setup_native(tmp_path)
    orphan, neighbor, valid = list(engine.backend.records.values())
    excluded = engine.backend.add("Synthetic observation.", user_id="u1", scope=MemoryScope.SHARED,
                                  persona_id=None, metadata={"journal_kind": "observation"})[0]
    save_report(store, engine, [group([orphan, neighbor]), group([valid])], [orphan, neighbor, valid])
    if change == "changed":
        engine.backend.update(neighbor.id, content="Synthetic corrected fact.")
    else:
        engine.backend.delete(neighbor.id)
    store.request("u1")
    drain(worker)
    report = store.latest("u1", ready_only=True)["report"]
    sent = [batch[0]["id"] for batch in model.batches]
    assert orphan.id in sent and valid.id not in sent and excluded.id not in sent
    assert len(sent) == (2 if change == "changed" else 1)
    assert report["covered_seeds"][excluded.id] == memory_version(excluded)
    assert report["covered_seeds"][orphan.id] == report["versions"][orphan.id] == memory_version(orphan)
    assert group([valid]) in report["groups"] and report["remaining"] == 0
    # The current mobile projection separately counts non-seed observations.
    assert mobile_coverage(engine, store)["unorganized"] == 1


@pytest.mark.parametrize("survivor", ["report", "ledger"])
def test_surviving_group_avoids_redundant_request_after_other_group_invalidates(tmp_path: Path, survivor: str) -> None:
    engine, store, model, worker = setup_native(tmp_path, count=2)
    seed, neighbor = list(engine.backend.records.values())
    groups = [group([seed, neighbor])]
    if survivor == "report":
        groups.append(group([seed]))
    else:
        engine.store.execute("INSERT INTO initialization_seeds(id,version,state,sent_at,report_json) "
                             "VALUES (?,?,'done',?,?)",
                             (seed.id, memory_version(seed), "2026-01-01T00:00:00+00:00", json.dumps(group([seed]))))
    save_report(store, engine, groups, [seed, neighbor])
    engine.backend.delete(neighbor.id)
    before = engine.store.rows("SELECT * FROM initialization_seeds")
    store.request("u1")
    drain(worker)
    assert not model.batches
    assert store.latest("u1", ready_only=True)["report"]["groups"] == [group([seed])]
    assert engine.store.rows("SELECT * FROM initialization_seeds") == before
    assert mobile_coverage(engine, store)["organized"] == 1


@pytest.mark.parametrize("state", ["done", "retry_done"])
def test_successful_history_rebuild_uses_daily_budget_and_preserves_terminal_ledgers(tmp_path: Path, state: str) -> None:
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=2, daily_chars=100000,
        model=FailingOrganization({1} if state == "retry_done" else ()))
    store.request("u1")
    drain(worker)
    if state == "retry_done":
        failed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state='failed'")[0]
        assert engine.store.recover_initialization_seeds([(failed["id"], failed["version"])]) == 1
        assert journal.resume_initialization()
        drain(worker)
    seed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state=? ORDER BY id", (state,))[0]
    neighbor = next(mid for mid in engine.backend.records if mid != seed["id"])
    engine.backend.delete(neighbor)
    before = {table: engine.store.rows(f"SELECT * FROM {table}") for table in (
        "initialization_evidence", "initialization_seeds", "initialization_recovery")}
    initial_chars, day_chars, calls = budget(engine, "initialization:"), budget(engine, "day:"), len(model.batches)
    store.request("u1")
    drain(worker)
    assert len(model.batches) == calls + 1 and model.batches[-1][0]["id"] == seed["id"]
    assert budget(engine, "day:") > day_chars and budget(engine, "initialization:") == initial_chars
    assert all(engine.store.rows(f"SELECT * FROM {table}") == rows for table, rows in before.items())
    assert engine.initialization_status()["state"] == "completed" and not journal.resume_initialization()
    assert mobile_coverage(engine, store)["unorganized"] == 0
    store.request("u1")
    drain(worker)
    assert len(model.batches) == calls + 1


@pytest.mark.parametrize("changed_versions", [False, True])
def test_stopped_exact_versions_stay_stopped_without_blocking_new_versions(tmp_path: Path, changed_versions: bool) -> None:
    engine, store, model, worker = setup_native(tmp_path, count=6)
    *stopped, ordinary, neighbor = list(engine.backend.records.values())
    for item, state in zip(stopped, ("failed", "spent", "retry_failed", "retry_spent")):
        engine.store.execute("INSERT INTO initialization_seeds(id,version,state,sent_at) VALUES (?,?,?,?)",
                             (item.id, memory_version(item), state, "2026-01-01T00:00:00+00:00"))
        if state.startswith("retry_"):
            engine.store.execute("INSERT INTO initialization_recovery VALUES (?,?,?,?)",
                                 (item.id, memory_version(item), "2026-01-02T00:00:00+00:00", "2026-01-02T00:01:00+00:00"))
    save_report(store, engine, [group(list(engine.backend.records.values()))], [*stopped, ordinary, neighbor])
    engine.backend.delete(neighbor.id)
    if changed_versions:
        for item in stopped:
            engine.backend.update(item.id, content=item.content + " Corrected.")
    before = engine.store.rows("SELECT * FROM initialization_seeds")
    recovery = engine.store.rows("SELECT * FROM initialization_recovery")
    store.request("u1")
    drain(worker)
    report = store.latest("u1", ready_only=True)["report"]
    assert len(model.batches) == (5 if changed_versions else 1)
    assert report["stopped_seeds"] == ({} if changed_versions else {item.id: memory_version(item) for item in stopped})
    assert engine.store.rows("SELECT * FROM initialization_seeds") == before
    assert engine.store.rows("SELECT * FROM initialization_recovery") == recovery
    assert mobile_coverage(engine, store)["stopped"] == (0 if changed_versions else 4)
    assert report["remaining"] == 0


@pytest.mark.parametrize("recovering", [False, True])
def test_orphaned_pending_seed_keeps_its_initialization_send_identity(tmp_path: Path, recovering: bool) -> None:
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=1, model=FailingOrganization({1} if recovering else ()))
    seed = next(iter(engine.backend.records.values()))
    if recovering:
        store.request("u1")
        drain(worker)
        assert engine.store.recover_initialization_seeds([(seed.id, memory_version(seed))]) == 1
    neighbor = engine.backend.add("Synthetic former neighbor.", user_id="u1", scope=MemoryScope.SHARED,
                                  persona_id=None, metadata={"privacy": "cloud"})[0]
    save_report(store, engine, [group([seed, neighbor])], [seed])
    engine.backend.delete(neighbor.id)
    original_sent = engine.store.rows("SELECT sent_at FROM initialization_seeds")[0]["sent_at"]
    chars, calls = budget(engine, "initialization:"), len(model.batches)
    store.request("u1")
    drain(worker)
    rows = engine.store.rows("SELECT state,sent_at FROM initialization_seeds")
    assert rows[0]["state"] == ("retry_done" if recovering else "done")
    if recovering:
        assert rows[0]["sent_at"] == original_sent
        assert engine.store.rows("SELECT sent_at FROM initialization_recovery")[0]["sent_at"]
    else:
        assert rows[0]["sent_at"]
    assert budget(engine, "initialization:") > chars and budget(engine, "day:") == 0
    assert len(model.batches) == calls + 1 and not journal.resume_initialization()
    before = engine.store.rows("SELECT * FROM initialization_recovery")
    store.request("u1")
    drain(worker)
    assert len(model.batches) == calls + 1
    assert engine.store.rows("SELECT * FROM initialization_recovery") == before


def test_orphan_rebuild_defers_at_daily_budget_without_resetting_history(tmp_path: Path) -> None:
    engine, store, model, worker, journal = setup_organization(tmp_path, count=2, daily_chars=100000)
    store.request("u1")
    drain(worker)
    seed, neighbor = list(engine.backend.records.values())
    engine.backend.delete(neighbor.id)
    engine.store.reserve_daily(engine.policy, engine.policy.daily_chars)
    before = {table: engine.store.rows(f"SELECT * FROM {table}") for table in (
        "budgets", "initialization_seeds", "initialization_recovery", "egress_attempts")}
    calls = len(model.batches)
    store.request("u1")
    assert worker.process_next() and not worker.process_next()
    latest = store.latest("u1")
    assert latest["status"] == "pending" and latest["error_code"] == "journal_daily_budget"
    assert len(model.batches) == calls
    assert all(engine.store.rows(f"SELECT * FROM {table}") == rows for table, rows in before.items())
    assert mobile_coverage(engine, store)["unorganized"] == 1
    assert not journal.resume_initialization()


def test_orphan_rebuild_keeps_five_seed_bound_and_visible_continuation(tmp_path: Path) -> None:
    engine, store, model, worker = setup_native(tmp_path, count=8)
    *seeds, neighbor = list(engine.backend.records.values())
    save_report(store, engine, [group([seed, neighbor]) for seed in seeds], [*seeds, neighbor])
    engine.backend.delete(neighbor.id)
    store.request("u1")
    assert worker.process_next()
    report = store.latest("u1", ready_only=True)["report"]
    assert len(model.batches) == 5 and report["remaining"] == 2
    assert store.latest("u1")["status"] == "pending"
    assert mobile_coverage(engine, store)["unorganized"] == 2
    assert worker.process_next() and not worker.process_next()
    report = store.latest("u1", ready_only=True)["report"]
    assert len(model.batches) == 7 and report["remaining"] == 0
    assert len({batch[0]["id"] for batch in model.batches}) == 7
    assert mobile_coverage(engine, store)["organized"] == 7


def orphaned_native(tmp_path: Path) -> tuple[Any, ...]:
    engine, store, model, worker = setup_native(tmp_path, count=4)
    *seeds, neighbor = list(engine.backend.records.values())
    save_report(store, engine, [group([seed, neighbor]) for seed in seeds], [*seeds, neighbor])
    engine.backend.delete(neighbor.id)
    return engine, store, model, worker, seeds


def test_mid_round_budget_retains_paid_progress_and_resumes_after_reopen_next_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, store, _, worker, seeds = orphaned_native(tmp_path)

    class SharedBudget(ObservationModel):
        attempts = 0

        def complete_json_with_guard(self, messages, schema, *, before_send):
            self.attempts += 1
            if self.attempts == 2:
                engine.store.reserve_daily(engine.policy, engine.policy.daily_chars - budget(engine, "day:"))
            before_send()
            return self.complete(messages, [])

    model = SharedBudget()
    worker = MemoryOrganizer(worker._backend, store, model)
    run_id = store.request("u1")
    assert worker.process_next() and not worker.process_next()
    checkpoint = store.latest_report("u1")
    assert checkpoint["id"] == run_id and checkpoint["status"] == "pending"
    assert checkpoint["report"]["remaining"] == 2 and len(model.batches) == 1
    assert set(checkpoint["report"]["covered_seeds"]) == {seeds[0].id}
    assert checkpoint["error_code"] == "journal_daily_budget" and budget(engine, "day:") == engine.policy.daily_chars
    store.close()
    reopened = OrganizationStore(tmp_path / "operations.sqlite3")
    resumed = MemoryOrganizer(worker._backend, reopened, model)
    before = reopened.latest("u1")
    assert not resumed.process_next() and reopened.latest("u1") == before

    class NextDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz or timezone.utc) + timedelta(days=1)

    monkeypatch.setattr("riji_agent.memory.organization_store.datetime", NextDay)
    monkeypatch.setattr("riji_agent.memory.journal_store.datetime", NextDay)
    drain(resumed)
    assert [batch[0]["id"] for batch in model.batches] == [item.id for item in seeds]
    assert reopened.latest("u1")["status"] == "ready"
    assert reopened.latest_report("u1")["report"]["remaining"] == 0
    assert len(engine.store.rows("SELECT * FROM budgets WHERE key LIKE 'day:%'")) == 2


@pytest.mark.parametrize("failure", ["invalid_output", "exception"])
def test_mid_round_failure_retains_successful_seed_for_explicit_reopen(tmp_path: Path, failure: str) -> None:
    engine, store, _, worker, seeds = orphaned_native(tmp_path)
    canary = "synthetic-rejected-response-do-not-store"

    class FailingSecond(ObservationModel):
        def __init__(self):
            super().__init__()
            self.sent = []

        def complete_json_with_guard(self, messages, schema, *, before_send):
            before_send()
            self.sent.append(json.loads(messages[-1]["content"])[0]["id"])
            if len(self.sent) == 2:
                if failure == "exception":
                    raise RuntimeError(canary)
                return AssistantTurn(canary)
            return self.complete(messages, [])

    model = FailingSecond()
    worker = MemoryOrganizer(worker._backend, store, model)
    run_id = store.request("u1")
    assert worker.process_next() and not worker.process_next()
    checkpoint = store.latest_report("u1")
    assert checkpoint["id"] == run_id and checkpoint["status"] == "failed"
    assert checkpoint["report"]["remaining"] == 2
    assert set(checkpoint["report"]["covered_seeds"]) == {seeds[0].id}
    assert canary not in "\n".join(store._conn.iterdump())
    used = budget(engine, "day:")
    ledger = engine.store.rows("SELECT * FROM initialization_seeds")
    store.close()
    reopened = OrganizationStore(tmp_path / "operations.sqlite3")
    reopened.request("u1")
    drain(MemoryOrganizer(worker._backend, reopened, model))
    assert model.sent == [seeds[0].id, seeds[1].id, seeds[1].id, seeds[2].id]
    assert budget(engine, "day:") > used
    assert engine.store.rows("SELECT * FROM initialization_seeds") == ledger
    assert reopened.latest_report("u1")["report"]["remaining"] == 0


def test_budget_before_first_send_checkpoints_reconciled_old_groups_and_remaining(tmp_path: Path) -> None:
    engine, store, model, worker = setup_native(tmp_path, count=4)
    orphan, other, valid, neighbor = list(engine.backend.records.values())
    save_report(store, engine, [group([orphan, neighbor]), group([other, neighbor]), group([valid])],
                [orphan, other, valid, neighbor])
    engine.backend.delete(neighbor.id)
    engine.store.reserve_daily(engine.policy, engine.policy.daily_chars)
    before = engine.store.rows("SELECT * FROM budgets")
    run_id = store.request("u1")
    assert worker.process_next() and not worker.process_next()
    checkpoint = store.latest_report("u1")
    assert checkpoint["id"] == run_id and checkpoint["status"] == "pending"
    assert checkpoint["report"]["groups"] == [group([valid])]
    assert checkpoint["report"]["covered_seeds"] == {valid.id: memory_version(valid)}
    assert checkpoint["report"]["remaining"] == 2 and not model.batches
    assert engine.store.rows("SELECT * FROM budgets") == before
    assert mobile_coverage(engine, store)["unorganized"] == 2


def test_overlapping_rebuild_groups_preserve_coverage_when_superseding_a_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, store, model, worker, seeds = orphaned_native(tmp_path)
    first, second, third = seeds
    related = {first.content: [second], second.content: [first, third], third.content: [second]}
    engine.backend.search = lambda query, **kwargs: tuple(related[query])
    checkpoints = []
    persist = store.record_checkpoint

    def checkpoint(run_id, report):
        checkpoints.append(json.loads(json.dumps(report)))
        persist(run_id, report)

    monkeypatch.setattr(store, "record_checkpoint", checkpoint)
    store.request("u1")
    drain(worker)
    assert [len(item["groups"]) for item in checkpoints] == [0, 1, 1, 2]
    for value in checkpoints:
        assert set(value["covered_seeds"]) <= {mid for current in value["groups"] for mid in current["versions"]}
    assert [set(item["id"] for item in batch) for batch in model.batches] == [
        {first.id, second.id}, {first.id, second.id, third.id}, {second.id, third.id}]
    assert mobile_coverage(engine, store)["organized"] == 3
    store.request("u1")
    drain(worker)
    assert len(model.batches) == 3 and store.latest_report("u1")["report"]["remaining"] == 0


def successful_history(tmp_path: Path, recovered: bool) -> tuple[Any, ...]:
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=2, daily_chars=100000, model=FailingOrganization({1} if recovered else ()))
    store.request("u1")
    drain(worker)
    if recovered:
        failed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state='failed'")[0]
        engine.store.recover_initialization_seeds([(failed["id"], failed["version"])])
        assert journal.resume_initialization()
        drain(worker)
    state = "retry_done" if recovered else "done"
    seed = engine.store.rows("SELECT id FROM initialization_seeds WHERE state=? ORDER BY id", (state,))[0]["id"]
    engine.backend.delete(next(mid for mid in engine.backend.records if mid != seed))
    evidence = engine.store.rows("SELECT evidence_id FROM supports WHERE memory_id=?", (seed,))[0]["evidence_id"]
    source = engine.policy.root / engine.current_evidence(evidence).path
    return engine, store, worker, seed, source


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("change", ["pause", "revoke", "source", "private", "local", "none", "memory"])
def test_ordinary_successful_history_rechecks_guard_without_touching_ledgers(
    tmp_path: Path, recovered: bool, change: str,
) -> None:
    engine, store, worker, seed, source = successful_history(tmp_path, recovered)
    before = {table: engine.store.rows(f"SELECT * FROM {table}") for table in (
        "budgets", "initialization_seeds", "initialization_recovery", "egress_attempts")}

    class ChangeBeforeSend(ObservationModel):
        def complete_json_with_guard(self, messages, schema, *, before_send):
            if change == "pause":
                engine.store.set_control("paused", "1")
            elif change == "revoke":
                engine.privacy.revoke()
            elif change == "memory":
                engine.backend.update(seed, content="Synthetic corrected memory before send.")
            elif change == "source":
                source.write_text(source.read_text() + "\nChanged source version.\n")
            else:
                mark = "private: true" if change == "private" else f"memory: {change}"
                source.write_text("---\n" + mark + "\n---\n" + source.read_text())
            before_send()
            pytest.fail("invalidated ordinary memory must not be sent")

    model = ChangeBeforeSend()
    store.request("u1")
    guarded = MemoryOrganizer(worker._backend, store, model)
    assert guarded.process_next() and not guarded.process_next()
    assert not model.batches
    assert all(engine.store.rows(f"SELECT * FROM {table}") == rows for table, rows in before.items())
    assert store.latest_report("u1")["report"]["groups"] == []


@pytest.mark.parametrize("historical", ["report", "ledger"])
def test_accepted_legacy_entry_extensions_are_normalized_without_losing_valid_groups(
    tmp_path: Path, historical: str,
) -> None:
    engine, store, model, worker = setup_native(tmp_path, count=2)
    records = list(engine.backend.records.values())
    legacy = group(records)
    canary = "synthetic-unrecognized-extension-do-not-retain"
    legacy["topics"][0]["legacy_extension"] = canary
    legacy["comparisons"] = [{"kind": "related", "evidence_ids": [item.id for item in records],
                              "reason": "Synthetic relationship.", "legacy_extension": canary}]
    if historical == "ledger":
        engine.store.execute("INSERT INTO initialization_seeds(id,version,state,sent_at,report_json) "
                             "VALUES (?,?,'done',?,?)", (records[0].id, memory_version(records[0]),
                             "2026-01-01T00:00:00+00:00", json.dumps(legacy)))
    save_report(store, engine, [legacy] if historical == "report" else [], records)
    before = engine.store.rows("SELECT * FROM initialization_seeds")
    store.request("u1")
    drain(worker)
    report = store.latest_report("u1")["report"]
    assert not model.batches and report["remaining"] == 0
    assert len(report["groups"]) == 1 and len(report["groups"][0]["comparisons"]) == 1
    assert canary not in json.dumps(report)
    assert engine.store.rows("SELECT * FROM initialization_seeds") == before
    assert mobile_coverage(engine, store)["organized"] == 2


@pytest.mark.parametrize("damage", ["topics", "scope", "observations"])
def test_invalid_old_group_is_dropped_before_base_checkpoint_and_rebuilt(tmp_path: Path, damage: str) -> None:
    engine, store, model, worker = setup_native(tmp_path, count=2)
    records = list(engine.backend.records.values())
    stale = group(records)
    if damage == "topics":
        stale["topics"][0]["evidence_ids"] = [records[0].id]
    elif damage == "scope":
        stale["scope"] = "persona"
    else:
        stale["observations"] = [{"summary": "Synthetic invalid observation."}]
    save_report(store, engine, [stale], records)
    store.request("u1")
    drain(worker)
    assert len(model.batches) == 2
    assert store.latest_report("u1")["report"]["remaining"] == 0
    assert mobile_coverage(engine, store)["organized"] == 2


def test_new_valid_model_entry_extensions_do_not_block_checkpoint(tmp_path: Path) -> None:
    engine, store, _, worker = setup_native(tmp_path, count=1)
    canary = "synthetic-new-entry-extension-do-not-retain"

    class ExtendedEntry(ObservationModel):
        def complete(self, messages, tools):
            payload = json.loads(super().complete(messages, tools).content)
            payload["topics"][0]["legacy_extension"] = canary
            return AssistantTurn(json.dumps(payload))

    model = ExtendedEntry()
    store.request("u1")
    drain(MemoryOrganizer(worker._backend, store, model))
    assert len(model.batches) == 1
    assert store.latest("u1")["status"] == "ready"
    assert canary not in "\n".join(store._conn.iterdump())
