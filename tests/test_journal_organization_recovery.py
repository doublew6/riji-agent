from __future__ import annotations

import json
from dataclasses import replace

import pytest

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.journal_organization import JournalOrganization
from riji_agent.memory.journal_types import JournalMemoryError
from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.memory.organization_store import OrganizationStore
from riji_agent.models.types import AssistantTurn, LLMError
from test_journal_initialization_budget import budget, grant, initialized
from test_journal_lifecycle import attached_service
from test_journal_organization import ObservationModel


class FailingOrganization(ObservationModel):
    def __init__(self, failures=()):
        super().__init__()
        self.failures = set(failures)

    def complete(self, messages, tools):
        result = super().complete(messages, tools)
        if len(self.batches) in self.failures:
            return AssistantTurn("invalid structure")
        return result


def setup_organization(tmp_path, *, count=7, model=None, daily_chars=100):
    engine, _ = initialized(tmp_path, count=count, organization=True, daily_chars=daily_chars)
    for _ in range(count):
        assert engine.process_next()
    assert engine.initialization_status()["organization"] == {"pending": count}
    service = attached_service(tmp_path, engine)
    store = service.operations.organization
    model = model or FailingOrganization()
    return (engine, store, model, MemoryOrganizer(service.backend, store, model),
            JournalOrganization(service.backend, store, model))


def drain(organizer, maximum=20):
    for _ in range(maximum):
        if not organizer.process_next():
            return
    pytest.fail("organization queue did not become idle")


@pytest.mark.parametrize("failures", [{1}, {5}, set(range(1, 8))])
def test_sent_failure_continues_remaining_fixed_seeds_without_retrying_spent_ones(tmp_path, failures):
    engine, store, model, worker, journal = setup_organization(
        tmp_path, model=FailingOrganization(failures))
    initial_chars = budget(engine, "initialization:")
    store.request("u1")
    drain(worker)
    states = engine.initialization_status()
    assert states["state"] == "blocked"
    assert states["organization"]["failed"] == len(failures)
    assert states["organization"].get("done", 0) == 7 - len(failures)
    assert len(model.batches) == 7
    assert len({batch[0]["id"] for batch in model.batches}) == 7
    assert budget(engine, "day:") == 0
    assert budget(engine, "initialization:") > initial_chars
    report = store.latest("u1", ready_only=True)["report"]
    assert report["processed"] == 7 - len(failures)
    assert len(report["stopped_seeds"]) == len(failures)
    assert report["remaining"] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM memory_organization_runs WHERE status='failed'").fetchone()[0] == len(failures)
    assert not journal.resume_initialization()
    store.request("u1")  # A later notification still cannot retry old failed versions.
    drain(worker)
    assert len(model.batches) == 7 and budget(engine, "day:") == 0


def test_resume_repairs_only_missing_schedule_and_keeps_the_fixed_ledger(tmp_path):
    engine, store, model, worker, journal = setup_organization(tmp_path, count=3)
    first = engine.store.rows("SELECT id,version FROM initialization_seeds ORDER BY id LIMIT 1")[0]
    engine.store.reserve_daily(engine.policy, 10, initialization_seed=(first["id"], first["version"]))
    engine.store.finish_initialization_seed(first["id"], first["version"], "failed")
    before = engine.store.rows("SELECT * FROM initialization_seeds")
    chars = budget(engine, "initialization:")
    assert journal.resume_initialization()
    assert not journal.resume_initialization()
    assert engine.store.rows("SELECT * FROM initialization_seeds") == before
    assert budget(engine, "initialization:") == chars and budget(engine, "day:") == 0
    drain(worker)
    assert len(model.batches) == 2
    assert first["id"] not in {batch[0]["id"] for batch in model.batches}
    assert engine.initialization_status()["organization"] == {"done": 2, "failed": 1}


def test_failure_before_send_stays_idle_until_explicit_resume(tmp_path, monkeypatch):
    engine, store, model, worker, journal = setup_organization(tmp_path, count=2)
    original = journal.backend.search
    def unavailable(*args, **kwargs):
        raise MemoryBackendError("mem0_unavailable")
    monkeypatch.setattr(journal.backend, "search", unavailable)
    store.request("u1")
    assert worker.process_next() and not worker.process_next()
    assert not model.batches
    assert engine.initialization_status()["organization"] == {"pending": 2}
    monkeypatch.setattr(journal.backend, "search", original)
    assert journal.resume_initialization()
    drain(worker)
    assert engine.initialization_status()["state"] == "completed"


@pytest.mark.parametrize("code", ["codex_quota_exhausted", "codex_login_required"])
def test_resume_cannot_wake_provider_backoff_or_consume_an_unsent_seed(tmp_path, code):
    class Deferred(ObservationModel):
        def complete_with_guard(self, messages, tools, *, before_send):
            raise LLMError(code)
    engine, store, model, worker, journal = setup_organization(tmp_path, count=1, model=Deferred())
    chars = budget(engine, "initialization:")
    store.request("u1")
    assert worker.process_next()
    before = store.latest("u1")
    assert before["status"] == "pending" and before["error_code"] == code
    assert not journal.resume_initialization()
    assert store.latest("u1") == before and not worker.process_next()
    assert engine.initialization_status()["organization"] == {"pending": 1}
    assert not model.batches and budget(engine, "initialization:") == chars


def test_idle_request_preserves_active_workers_and_other_users(tmp_path):
    store = OrganizationStore(tmp_path / "organization.sqlite3")
    first = store.request_if_idle("u1")
    assert first is not None and store.request_if_idle("u1") is None
    run = store.claim()
    assert run["id"] == first and store.request_if_idle("u1") is None
    assert store.request_if_idle("u2") is not None
    store.defer(first, 3600, "journal_daily_budget")
    before = store.latest("u1")
    assert store.request_if_idle("u1") is None and store.latest("u1") == before
    store.finish(first, None)
    assert store.request_if_idle("u1") is not None


def test_claim_waits_for_same_user_but_keeps_other_users_runnable(tmp_path):
    store = OrganizationStore(tmp_path / "organization.sqlite3")
    first = store.request("u1")
    assert store.claim()["id"] == first
    following = store.request("u1")
    assert store.claim() is None
    other = store.request("u2")
    assert store.claim()["id"] == other
    assert store.claim() is None
    store.finish(first, None)
    assert store.claim()["id"] == following


@pytest.mark.parametrize("change", ["pause", "revoke"])
def test_resume_respects_existing_permissions_and_pause(tmp_path, change):
    engine, store, model, worker, journal = setup_organization(tmp_path, count=1)
    if change == "pause":
        engine.store.set_control("paused", "1")
    else:
        engine.privacy.revoke()
    with pytest.raises(JournalMemoryError):
        journal.resume_initialization()
    assert store.latest("u1") is None and not model.batches


def test_structured_schema_is_input_bound_counted_and_still_semantically_validated(tmp_path):
    class Structured(FailingOrganization):
        def complete_json_with_guard(self, messages, schema, *, before_send):
            self.schema = schema
            self.request_chars = sum(len(message["content"]) for message in messages) + len(json.dumps(schema, ensure_ascii=False))
            before_send()
            result = json.loads(super().complete(messages, []).content)
            mid = result["topics"][0]["evidence_ids"][0]
            # Structurally shaped but not two independent events: semantic checks remain mandatory.
            result["observations"] = [{"summary": "Possibly relevant.", "support_ids": [mid, mid],
                                       "counter_ids": [], "limitation": "Needs verification."}]
            return AssistantTurn(json.dumps(result))
    engine, store, model, worker, journal = setup_organization(tmp_path, count=1, model=Structured())
    initial_chars = budget(engine, "initialization:")
    store.request("u1")
    drain(worker)
    ids = [item["id"] for item in model.batches[0]]
    properties = model.schema["properties"]
    assert model.schema["additionalProperties"] is False
    assert set(model.schema["required"]) == {"topics", "comparisons", "time_bound_ids", "observations"}
    for field in ("topics", "comparisons"):
        item = properties[field]["items"]
        assert item["additionalProperties"] is False
        assert item["properties"]["evidence_ids"]["items"]["enum"] == ids
    observation = properties["observations"]["items"]
    assert observation["properties"]["support_ids"]["items"]["enum"] == ids
    assert properties["time_bound_ids"]["items"]["enum"] == ids
    assert budget(engine, "initialization:") - initial_chars == model.request_chars
    assert engine.initialization_status()["organization"] == {"failed": 1}
    assert len(model.batches) == 1


def test_guard_after_queue_wait_rechecks_permissions_before_sending(tmp_path):
    class Revoking(ObservationModel):
        def complete_json_with_guard(self, messages, schema, *, before_send):
            engine.privacy.revoke()
            before_send()
            return self.complete(messages, [])
    engine, store, model, worker, journal = setup_organization(tmp_path, count=1, model=Revoking())
    before = budget(engine, "initialization:")
    store.request("u1")
    assert worker.process_next()
    assert not model.batches and budget(engine, "initialization:") == before
    assert engine.store.rows("SELECT state FROM initialization_seeds") == [{"state": "pending"}]


@pytest.mark.parametrize("retry_fails", [False, True])
def test_explicit_failed_recovery_uses_one_initialization_attempt_without_reopening_batch(tmp_path, retry_fails):
    model = FailingOrganization({1, 3} if retry_fails else {1})
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=2, model=model, daily_chars=100000)
    store.request("u1")
    drain(worker)
    assert engine.initialization_status()["state"] == "blocked" and len(model.batches) == 2
    failed = engine.store.rows("SELECT id,version,sent_at FROM initialization_seeds WHERE state='failed'")[0]
    refs = [(failed["id"], failed["version"])]
    initial_chars = budget(engine, "initialization:")
    assert engine.store.recover_initialization_seeds(refs) == 1
    assert engine.store.recover_initialization_seeds(refs) == 0
    assert not engine.store.initialization_active(engine.policy)
    assert journal.resume_initialization()
    drain(worker)
    result = engine.initialization_status()
    assert result["state"] == ("blocked" if retry_fails else "completed")
    assert result["organization"] == {"done": 1, "retry_failed" if retry_fails else "retry_done": 1}
    assert engine.store.rows("SELECT sent_at FROM initialization_seeds WHERE id=?", (failed["id"],))[0]["sent_at"] == failed["sent_at"]
    assert len(model.batches) == 3 and budget(engine, "initialization:") > initial_chars
    assert budget(engine, "day:") == 0
    assert engine.store.recover_initialization_seeds(refs) == 0
    assert not journal.resume_initialization()
    report = store.latest("u1", ready_only=True)["report"]
    assert report["remaining"] == 0 and report["processed"] == (1 if retry_fails else 2)
    store.request("u1")
    drain(worker)
    assert len(model.batches) == 3


def test_disabled_initialization_recovery_cannot_bypass_daily_cap(tmp_path):
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=1, model=FailingOrganization({1}), daily_chars=100000)
    store.request("u1")
    drain(worker)
    failed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state='failed'")[0]
    engine.store.reserve_daily(engine.policy, 100000)
    initial_chars = budget(engine, "initialization:")
    assert engine.store.recover_initialization_seeds([(failed["id"], failed["version"])]) == 1
    engine.policy = replace(engine.policy, initialization_unlimited=False)
    grant(engine, organization=True)
    assert journal.resume_initialization() and worker.process_next()
    assert not worker.process_next() and not journal.resume_initialization()
    assert len(model.batches) == 1 and budget(engine, "initialization:") == initial_chars
    assert budget(engine, "day:") == 100000
    assert engine.store.rows("SELECT state FROM initialization_seeds") == [{"state": "retry_pending"}]
    assert store.latest("u1")["error_code"] == "journal_daily_budget"


def test_full_daily_budget_cannot_block_unsent_seeds_or_registered_initialization_recovery(tmp_path):
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=3, model=FailingOrganization({1}), daily_chars=100000)
    store.request("u1")
    assert worker.process_next()
    failed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state='failed'")[0]
    assert engine.store.recover_initialization_seeds([(failed["id"], failed["version"])]) == 1
    engine.store.reserve_daily(engine.policy, 100000)
    drain(worker)
    assert len(model.batches) == 4
    assert engine.initialization_status()["organization"] == {"done": 2, "retry_done": 1}
    assert store.latest("u1")["error_code"] is None
    assert budget(engine, "day:") == 100000 and not journal.resume_initialization()


def test_losing_concurrent_send_cannot_consume_or_finish_the_winning_retry(tmp_path):
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=1, model=FailingOrganization({1}), daily_chars=100000)
    store.request("u1")
    drain(worker)
    failed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state='failed'")[0]
    ref = (failed["id"], failed["version"])
    assert engine.store.recover_initialization_seeds([ref]) == 1
    batch = [journal.backend.get(failed["id"])]
    loser_model = ObservationModel()
    loser = JournalOrganization(journal.backend, store, loser_model)
    class Winner(ObservationModel):
        def complete_json_with_guard(self, messages, schema, *, before_send):
            before_send()
            charged = budget(engine, "initialization:")
            with pytest.raises(JournalMemoryError, match="journal_recovery_already_sent"):
                loser._call(batch, initialization_seed=ref)
            assert budget(engine, "initialization:") == charged
            assert engine.store.rows("SELECT state FROM initialization_seeds") == [{"state": "retry_spent"}]
            return self.complete(messages, [])
    winner_model = Winner()
    winner = JournalOrganization(journal.backend, store, winner_model)
    initial_chars = budget(engine, "initialization:")
    winner._call(batch, initialization_seed=ref)
    assert not loser_model.batches and len(winner_model.batches) == 1
    assert engine.store.rows("SELECT state FROM initialization_seeds") == [{"state": "retry_done"}]
    assert budget(engine, "day:") == 0 and budget(engine, "initialization:") > initial_chars


@pytest.mark.parametrize('change', ['pause', 'revoke', 'changed_source', 'private', 'local', 'none'])
def test_unlimited_recovery_rechecks_permissions_and_source_at_actual_send(tmp_path, change):
    engine, store, model, worker, journal = setup_organization(
        tmp_path, count=1, model=FailingOrganization({1}), daily_chars=100000)
    store.request('u1')
    drain(worker)
    failed = engine.store.rows("SELECT id,version FROM initialization_seeds WHERE state='failed'")[0]
    ref = failed['id'], failed['version']
    engine.store.recover_initialization_seeds([ref])
    engine.store.reserve_daily(engine.policy, 100000)
    before = engine.store.rows('SELECT * FROM budgets ORDER BY key')
    attempts = engine.store.rows('SELECT * FROM egress_attempts')
    source = next(engine.policy.root.rglob('*.md'))

    class ChangeBeforeSend(ObservationModel):
        def complete_json_with_guard(self, messages, schema, *, before_send):
            if change == 'pause':
                engine.store.set_control('paused', '1')
            elif change == 'revoke':
                engine.privacy.revoke()
            elif change == 'changed_source':
                source.write_text(source.read_text() + '\nChanged source version.\n')
            else:
                mark = 'private: true' if change == 'private' else f'memory: {change}'
                source.write_text('---\n' + mark + '\n---\n' + source.read_text())
            before_send()
            raise AssertionError('invalid recovery content must never be sent')

    provider = ChangeBeforeSend()
    recovering = JournalOrganization(journal.backend, store, provider)
    with pytest.raises(JournalMemoryError):
        recovering._call([journal.backend.get(ref[0])], initialization_seed=ref)
    assert not provider.batches
    assert engine.store.rows('SELECT * FROM budgets ORDER BY key') == before
    assert engine.store.rows('SELECT * FROM egress_attempts') == attempts
    assert engine.store.rows('SELECT state FROM initialization_seeds') == [{'state': 'retry_pending'}]
    assert engine.store.rows('SELECT sent_at FROM initialization_recovery') == [{'sent_at': None}]
