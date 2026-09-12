from datetime import datetime, timezone
from dataclasses import replace

import pytest

from riji_agent.memory.journal_types import JournalMemoryError
from test_journal_initialization_budget import initialized, budget


def failed_evidence(engine):
    row = engine.store.rows("SELECT id,version FROM evidence ORDER BY id LIMIT 1")[0]
    engine.store.execute("UPDATE evidence SET status='failed',attempts=3,error='journal_invalid_model_json' "
                         "WHERE id=?", (row['id'],))
    engine.advance_initialization()
    return row['id'], row['version']


def failed_seed(engine):
    engine.store.track_initialization_seed(engine.policy, 'synthetic-seed', 'version-1')
    engine.store.reserve_daily(engine.policy, 25, initialization_seed=('synthetic-seed', 'version-1'))
    engine.store.finish_initialization_seed('synthetic-seed', 'version-1', 'failed')
    return 'synthetic-seed', 'version-1'


def test_targeted_evidence_retry_preserves_other_jobs_caches_and_budget(tmp_path):
    engine, _ = initialized(tmp_path, count=2, daily_chars=100000)
    ref = failed_evidence(engine)
    engine.store.execute("UPDATE evidence SET extracted='[]' WHERE id=?", (ref[0],))
    untouched = engine.store.rows("SELECT * FROM evidence WHERE id<>?", (ref[0],))
    engine.store.reserve_daily(engine.policy, 100)
    assert engine.store.retry_failed_evidence([ref, ref, (ref[0], 'stale')]) == 1
    assert engine.store.retry_failed_evidence([ref]) == 0
    assert engine.store.rows("SELECT * FROM evidence WHERE id<>?", (ref[0],)) == untouched
    assert engine.store.rows("SELECT extracted FROM evidence WHERE id=?", (ref[0],))[0]['extracted'] == '[]'
    assert budget(engine, 'day:') == 100
    assert not engine.store.initialization_member(engine.policy, *ref)


def test_recovered_evidence_can_settle_blocked_batch_without_free_retry(tmp_path):
    engine, model = initialized(tmp_path, daily_chars=100000)
    ref = failed_evidence(engine)
    assert engine.initialization_status()['state'] == 'blocked'
    assert engine.store.retry_failed_evidence([ref]) == 1
    assert not engine.store.initialization_member(engine.policy, *ref)
    assert engine.process_next() and model.calls
    assert budget(engine, 'day:') > 0 and budget(engine, 'initialization:') == 0
    assert engine.initialization_status()['state'] == 'completed'
    assert engine.initialization_status()['failed'] == 0
    assert engine.initialization_status()['completed'] == 1
    assert not engine.store.initialization_active(engine.policy)


def test_evidence_retry_rejects_stale_inactive_success_and_source_budget(tmp_path):
    engine, _ = initialized(tmp_path)
    ref = failed_evidence(engine)
    assert engine.store.retry_failed_evidence([(ref[0], 'stale')]) == 0
    for status, active in [('failed', 0), ('succeeded', 1), ('source_budget', 1)]:
        engine.store.execute("UPDATE evidence SET status=?,active=? WHERE id=?", (status, active, ref[0]))
        assert engine.store.retry_failed_evidence([ref]) == 0


def test_failed_seed_recovery_uses_initialization_budget_and_keeps_original_send_audit(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    original = engine.store.rows('SELECT sent_at FROM initialization_seeds')[0]['sent_at']
    assert engine.store.recover_initialization_seeds([ref, ref]) == 1
    assert engine.store.recover_initialization_seeds([ref]) == 0
    assert not engine.store.initialization_seed(engine.policy, *ref)
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    assert budget(engine, 'initialization:') == 75 and budget(engine, 'day:') == 0
    assert engine.store.rows('SELECT state,sent_at FROM initialization_seeds')[0] == {
        'state': 'retry_spent', 'sent_at': original}
    assert engine.store.rows('SELECT sent_at FROM initialization_recovery')[0]['sent_at']
    engine.store.finish_initialization_seed(*ref, 'done', {'versions': {ref[0]: ref[1]}})
    assert engine.store.rows('SELECT state FROM initialization_seeds')[0]['state'] == 'retry_done'
    assert engine.store.recover_initialization_seeds([ref]) == 0


def test_disabled_initialization_daily_cap_defers_recovery_without_spending_seed(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=10)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    policy = replace(engine.policy, initialization_unlimited=False)
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        engine.store.reserve_daily(policy, 11, initialization_seed=ref)
    assert engine.store.rows('SELECT state FROM initialization_seeds')[0]['state'] == 'retry_pending'
    assert engine.store.rows('SELECT sent_at FROM initialization_recovery')[0]['sent_at'] is None
    assert budget(engine, 'day:') == 0


def test_failed_recovery_is_terminal_and_does_not_hot_retry(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    engine.store.finish_initialization_seed(*ref, 'failed')
    assert engine.store.rows('SELECT state FROM initialization_seeds')[0]['state'] == 'retry_failed'
    assert engine.store.recover_initialization_seeds([ref]) == 0


def test_suppressed_memory_is_not_retained_in_recovery_report(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    engine.store.execute("INSERT INTO suppression VALUES ('memory',?,?)", (ref[0], datetime.now(timezone.utc).isoformat()))
    engine.store.finish_initialization_seed(*ref, 'done', {'versions': {ref[0]: ref[1]}})
    assert engine.store.rows('SELECT report_json FROM initialization_seeds')[0]['report_json'] is None


def test_expired_recovery_is_failed_without_reopening_initialization(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    engine.store.execute("UPDATE initialization_recovery SET sent_at='2000-01-01T00:00:00+00:00'")
    engine.store.set_control('initialization_state', 'blocked')
    engine.advance_initialization()
    assert engine.store.rows('SELECT state FROM initialization_seeds')[0]['state'] == 'retry_failed'
    assert not engine.store.initialization_active(engine.policy)


def test_second_worker_cannot_spend_same_recovery_or_charge_again(tmp_path):
    from riji_agent.memory.journal_store import JournalMemoryStore
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    other = JournalMemoryStore(engine.store.path)
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    with pytest.raises(JournalMemoryError, match='journal_recovery_already_sent'):
        other.reserve_daily(engine.policy, 50, initialization_seed=ref)
    assert budget(engine, 'day:') == 0 and budget(engine, 'initialization:') == 75
    assert engine.store.rows('SELECT state FROM initialization_seeds')[0]['state'] == 'retry_spent'
    other.close()


@pytest.mark.parametrize('state', ['active', 'blocked', 'completed'])
def test_finite_recovery_is_unlimited_without_reopening_or_transferring_old_budget(tmp_path, state):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    engine.store.set_control('initialization_state', state)
    engine.store.reserve_daily(engine.policy, 100000)
    initial = budget(engine, 'initialization:')
    original = engine.store.rows('SELECT sent_at FROM initialization_seeds')[0]['sent_at']
    assert engine.store.initialization_recovery_member(engine.policy, *ref)
    assert engine.initialization_status()['recovery_pending'] == 1
    engine.store.reserve_daily(engine.policy, 100001, initialization_seed=ref)
    status = engine.initialization_status()
    assert status['recovery_pending'] == 0 and status['recovery_spent'] == 1
    assert status['recovery_active'] and status['exemption_active']
    assert status['state'] == state and status['active'] == (state == 'active')
    assert budget(engine, 'initialization:') == initial + 100001
    assert budget(engine, 'day:') == 100000
    assert engine.store.rows('SELECT sent_at FROM initialization_seeds')[0]['sent_at'] == original
    engine.store.finish_initialization_seed(*ref, 'done')
    assert not engine.initialization_status()['recovery_active']
    assert not engine.store.initialization_recovery_member(engine.policy, *ref)
    assert engine.store.recover_initialization_seeds([ref]) == 0
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        engine.store.reserve_daily(engine.policy, 1)


@pytest.mark.parametrize('change', ['disabled', 'policy_scope', 'stored_scope', 'frozen_scope', 'cancelled'])
def test_recovery_allowance_requires_matching_enabled_fixed_batch(tmp_path, change):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=10)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    policy = engine.policy
    if change == 'disabled':
        policy = replace(policy, initialization_unlimited=False)
    elif change == 'policy_scope':
        policy = replace(policy, date_from='2026-01-01')
    else:
        key = {'stored_scope': 'scope', 'frozen_scope': 'initialization_scope',
               'cancelled': 'initialization_state'}[change]
        engine.store.set_control(key, 'cancelled' if change == 'cancelled' else 'different-scope')
    before = engine.store.rows('SELECT * FROM initialization_recovery')
    assert not engine.store.initialization_recovery_member(policy, *ref)
    status = engine.store.initialization_status(policy)
    assert not status['recovery_active'] and status['recovery_pending'] == 0
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        engine.store.reserve_daily(policy, 11, initialization_seed=ref)
    assert engine.store.rows('SELECT * FROM initialization_recovery') == before
    assert budget(engine, 'day:') == 0 and budget(engine, 'initialization:') == 25


@pytest.mark.parametrize('change', ['missing_original', 'missing_registration', 'stale_registration',
                                   'missing_requested_at', 'spent_registration', 'terminal'])
def test_malformed_or_spent_recovery_cannot_send_or_charge_either_ledger(tmp_path, change):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    mutations = {
        'missing_original': 'UPDATE initialization_seeds SET sent_at=NULL',
        'missing_registration': 'DELETE FROM initialization_recovery',
        'stale_registration': "UPDATE initialization_recovery SET version='changed-version'",
        'missing_requested_at': "UPDATE initialization_recovery SET requested_at=''",
        'spent_registration': "UPDATE initialization_recovery SET sent_at='2000-01-01'",
        'terminal': "UPDATE initialization_seeds SET state='retry_failed'",
    }
    engine.store.execute(mutations[change])
    seeds = engine.store.rows('SELECT * FROM initialization_seeds')
    recovery = engine.store.rows('SELECT * FROM initialization_recovery')
    assert not engine.store.initialization_recovery_member(engine.policy, *ref)
    with pytest.raises(JournalMemoryError, match='journal_recovery_(invalid|already_sent)'):
        engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    assert engine.store.rows('SELECT * FROM initialization_seeds') == seeds
    assert engine.store.rows('SELECT * FROM initialization_recovery') == recovery
    assert budget(engine, 'day:') == 0 and budget(engine, 'initialization:') == 25


@pytest.mark.parametrize('ref', [('synthetic-seed', 'changed-version'), ('other-seed', 'version-1')])
def test_nonbatch_or_changed_memory_version_still_uses_the_daily_cap(tmp_path, ref):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=10)
    seed = failed_seed(engine)
    engine.store.recover_initialization_seeds([seed])
    assert not engine.store.initialization_recovery_member(engine.policy, *ref)
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        engine.store.reserve_daily(engine.policy, 11, initialization_seed=ref)
    assert budget(engine, 'initialization:') == 25 and budget(engine, 'day:') == 0
    assert engine.store.rows('SELECT state FROM initialization_seeds')[0]['state'] == 'retry_pending'


def test_completed_batch_recovers_stale_claim_without_reopening_or_hot_retry(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    ref = failed_seed(engine)
    engine.store.recover_initialization_seeds([ref])
    engine.store.execute("UPDATE initialization_evidence SET state='succeeded'")
    engine.store.set_control('initialization_state', 'completed')
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    engine.store.execute("UPDATE initialization_recovery SET sent_at='2000-01-01T00:00:00+00:00'")
    engine.advance_initialization()
    status = engine.initialization_status()
    assert status['state'] == 'blocked'
    assert not status['active'] and not status['recovery_active'] and not status['exemption_active']
    assert status['organization'] == {'retry_failed': 1}
    assert engine.store.recover_initialization_seeds([ref]) == 0


def test_disabled_valid_recovery_keeps_ordinary_accounting_and_original_send(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    original = engine.store.rows('SELECT sent_at FROM initialization_seeds')[0]['sent_at']
    engine.store.recover_initialization_seeds([ref])
    engine.store.reserve_daily(replace(engine.policy, initialization_unlimited=False), 50,
                               initialization_seed=ref)
    assert budget(engine, 'day:') == 50 and budget(engine, 'initialization:') == 25
    assert engine.store.rows('SELECT state,sent_at FROM initialization_seeds')[0] == {
        'state': 'retry_spent', 'sent_at': original}
