from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_organization import JournalOrganization
from riji_agent.memory.journal_store import JournalMemoryStore
from riji_agent.memory.journal_types import JournalMemoryError, JournalMemoryPolicy, content_key
from riji_agent.memory.organization import MemoryOrganizer, memory_version
from riji_agent.memory.models import MemoryScope
from riji_agent.models.types import AssistantTurn
from test_codex_configuration import settings_for
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note
from test_journal_organization import ObservationModel

PURPOSES = dict.fromkeys(('history', 'incremental', 'organization', 'recall'), True)


def grant(engine, *, organization=False):
    engine.privacy.grant(engine.privacy.binding, dict(PURPOSES, organization=organization))


def initialized(tmp_path, *, count=1, organization=False, daily_chars=100, **options):
    engine, model = runtime(tmp_path, initialization_unlimited=True, daily_chars=daily_chars, **options)
    for index in range(count):
        write_note(engine.policy.root, f'daily/2026-08-{index + 1:02}.md', f'准备学习产品设计并记录第{index + 1}次实践。')
    engine.scan()
    grant(engine, organization=organization)
    return engine, model


def budget(engine, prefix):
    return engine.store.rows('SELECT COALESCE(SUM(chars),0) AS n FROM budgets WHERE key LIKE ?', (prefix + '%',))[0]['n']


def test_defaults_and_audit_expose_the_actual_budget_policy(tmp_path):
    settings = settings_for(tmp_path)
    assert settings.journal_memory_daily_chars == 100000
    assert settings.journal_memory_initialization_unlimited is False
    assert JournalMemoryPolicy(tmp_path, 'u1', ('Notes',)).daily_chars == 100000
    engine, _ = initialized(tmp_path)
    event = json.loads(engine.store.rows("SELECT payload FROM privacy_events WHERE action='grant' ORDER BY id DESC LIMIT 1")[0]['payload'])
    assert event['daily_chars'] == 100 and event['initialization_unlimited'] is True
    assert event['initialization']['total'] == 1 and event['initialization']['state'] == 'active'


def test_unscanned_empty_grant_cannot_make_later_incremental_content_unlimited(tmp_path):
    engine, model = runtime(tmp_path, initialization_unlimited=True, daily_chars=100)
    write_note(engine.policy.root)
    engine.scan()
    assert engine.initialization_status()['state'] == 'awaiting_scan'
    assert not engine.store.rows('SELECT * FROM privacy_history')
    assert engine.process_next() and not model.calls
    assert engine.store.rows('SELECT error FROM evidence')[0]['error'] == 'journal_daily_budget'
    grant(engine)
    assert engine.initialization_status()['total'] == 1
    assert engine.process_next() and model.calls


def test_initial_extraction_and_relations_have_separate_accounting_and_close(tmp_path):
    engine, model = initialized(tmp_path, count=2)
    day = 'day:' + datetime.now(timezone.utc).date().isoformat()
    engine.store.execute('INSERT INTO budgets VALUES (?,100)', (day,))
    assert engine.process_next() and engine.process_next()
    assert len(model.calls) == 3
    assert budget(engine, 'day:') == 100
    assert budget(engine, 'initialization:') > 100
    assert engine.initialization_status()['state'] == 'completed'
    assert engine.initialization_status()['completed'] == 2
    assert {row['phase'] for row in engine.store.rows('SELECT phase FROM egress_attempts')} == {'extraction', 'relation'}


def test_source_cumulative_limit_remains_enforced_and_is_not_false_completion(tmp_path):
    engine, model = initialized(tmp_path, source_chars=5)
    assert engine.process_next() and not model.calls
    assert engine.store.rows('SELECT status,error FROM evidence')[0] == {'status': 'source_budget', 'error': 'journal_source_budget'}
    assert engine.initialization_status()['state'] == 'blocked'
    assert engine.initialization_status()['failed'] == 1
    assert budget(engine, 'initialization:') == 0
    assert not engine.store.progress()['initialized']


def test_initialization_can_pass_one_hundred_thousand_without_consuming_the_ordinary_day(tmp_path):
    engine, model = initialized(tmp_path, daily_chars=100000)
    day = datetime.now(timezone.utc).date().isoformat()
    engine.store.execute('INSERT INTO budgets VALUES (?,?)', ('initialization:' + day, 100001))
    engine.store.execute('INSERT INTO budgets VALUES (?,?)', ('day:' + day, 100000))
    assert engine.process_next() and model.calls
    assert budget(engine, 'initialization:') > 100001
    assert budget(engine, 'day:') == 100000
    assert engine.initialization_status()['state'] == 'completed'


def test_later_new_file_and_changed_source_version_never_join_the_fixed_batch(tmp_path):
    engine, model = initialized(tmp_path)
    frozen = engine.store.rows('SELECT id,version FROM initialization_evidence')
    write_note(engine.policy.root, 'daily/2026-07-01.md', '新增日记应遵循日常预算。')
    old_path = engine.policy.root / 'daily/2026-08-01.md'
    old_path.write_text(old_path.read_text() + '\nChanged outside the selected section.\n')
    os.utime(old_path, (time.time() - 3, time.time() - 3))
    engine.scan()
    grant(engine)
    assert engine.store.rows('SELECT id,version FROM initialization_evidence') == frozen
    assert engine.process_next() and engine.process_next()
    assert not model.calls and budget(engine, 'initialization:') == 0
    assert engine.initialization_status()['excluded'] == 1
    assert all(row['status'] == 'budget' for row in engine.store.rows('SELECT status FROM evidence WHERE active=1'))


def test_completed_batch_does_not_reopen_on_restart_scan_or_reauthorization(tmp_path):
    engine, model = initialized(tmp_path)
    engine.process_next()
    restarted = JournalMemoryEngine(engine.policy, JournalMemoryStore(engine.store.path), engine.backend, model)
    write_note(engine.policy.root, 'daily/2026-09-01.md', '新的工作计划只享有日常预算。')
    restarted.scan()
    grant(restarted)
    previous_calls = len(model.calls)
    assert restarted.process_next()
    assert len(model.calls) == previous_calls
    assert restarted.initialization_status()['state'] == 'completed'
    assert restarted.initialization_status()['total'] == 1


def test_scope_change_cancels_the_existing_batch_without_creating_another(tmp_path):
    engine, _ = initialized(tmp_path)
    changed = JournalMemoryEngine(replace(engine.policy, date_from='2026-01-01'), engine.store, engine.backend, engine.provider)
    changed.scan()
    grant(changed)
    assert changed.initialization_status()['state'] == 'cancelled'
    assert not changed.initialization_status()['active']
    assert changed.initialization_status()['total'] == 1


def test_grant_wakes_only_daily_budget_and_does_not_include_old_failures(tmp_path):
    engine, _ = runtime(tmp_path, initialization_unlimited=True)
    for day in range(1, 5):
        write_note(engine.policy.root, f'daily/2026-08-0{day}.md')
    engine.scan()
    ids = [row['id'] for row in engine.store.rows('SELECT id FROM evidence ORDER BY id')]
    for evidence_id, state, error in zip(ids, ('budget', 'failed', 'source_budget', 'retry'),
            ('journal_daily_budget', 'journal_processing_failed', 'journal_source_budget', 'codex_login_required')):
        engine.store.execute("UPDATE evidence SET status=?,error=?,attempts=2,available_at='2999-01-01' WHERE id=?", (state, error, evidence_id))
    grant(engine)
    rows = engine.store.rows('SELECT status,error,attempts FROM evidence ORDER BY id')
    assert rows[0] == dict(status='pending', error=None, attempts=2)
    assert [row['status'] for row in rows[1:]] == ['failed', 'source_budget', 'retry']
    assert engine.initialization_status()['total'] == 1


def test_increasing_the_ordinary_daily_limit_releases_only_its_budget_waits(tmp_path):
    engine, model = runtime(tmp_path, daily_chars=100)
    write_note(engine.policy.root)
    engine.scan()
    engine.process_next()
    assert engine.store.rows('SELECT status FROM evidence')[0]['status'] == 'budget'
    changed = JournalMemoryEngine(replace(engine.policy, daily_chars=100000), engine.store, engine.backend, model)
    assert not changed.process_next()
    grant(changed)
    assert changed.store.rows('SELECT status FROM evidence')[0]['status'] == 'pending'
    assert changed.process_next() and model.calls
    assert budget(changed, 'day:') > 100 and budget(changed, 'initialization:') == 0


def test_failed_batch_member_cannot_regain_exemption_via_an_ordinary_retry(tmp_path):
    engine, model = initialized(tmp_path, count=2)
    evidence_id = engine.store.rows('SELECT id FROM evidence ORDER BY id')[0]['id']
    engine.store.execute("UPDATE evidence SET status='failed',error='journal_processing_failed' WHERE id=?", (evidence_id,))
    engine.advance_initialization()
    engine.store.retry()
    row = engine.store.rows('SELECT id,version FROM evidence WHERE id=?', (evidence_id,))[0]
    assert not engine.store.initialization_member(engine.policy, row['id'], row['version'])
    assert engine.initialization_status()['active']


def test_revocation_at_actual_send_blocks_both_request_and_initialization_charge(tmp_path):
    engine, _ = initialized(tmp_path)
    class QueuedModel:
        def complete_with_guard(self, messages, tools, before_send):
            engine.privacy.revoke()
            before_send()
            raise AssertionError('revoked data must not be sent')
    engine.provider = QueuedModel()
    assert engine.process_next()
    assert budget(engine, 'initialization:') == 0
    assert not engine.store.rows('SELECT * FROM egress_attempts')


def test_source_edit_at_actual_send_blocks_the_initialization_charge(tmp_path):
    engine, _ = initialized(tmp_path)
    class QueuedModel:
        def complete_with_guard(self, messages, tools, before_send):
            write_note(engine.policy.root, 'daily/2026-08-01.md', '发送之前内容已变更。')
            before_send()
            raise AssertionError('changed data must not be sent')
    engine.provider = QueuedModel()
    assert engine.process_next()
    assert budget(engine, 'initialization:') == 0


def test_necessary_organization_is_separately_counted_and_closes_the_batch(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    service = attached_service(tmp_path, engine)
    model = ObservationModel()
    service.operations.organization.request('u1')
    before = budget(engine, 'initialization:')
    assert MemoryOrganizer(service.backend, service.operations.organization, model).process_next()
    assert model.batches and budget(engine, 'initialization:') > before
    assert budget(engine, 'day:') == 0
    assert engine.initialization_status()['state'] == 'completed'
    assert engine.initialization_status()['organization'] == {'done': 1}


def test_fixed_organization_seed_cannot_be_used_twice_or_with_a_new_version(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    item = next(iter(engine.backend.records.values()))
    seed = (item.id, memory_version(item))
    engine.store.reserve_daily(engine.policy, 1000, initialization_seed=seed)
    spent = budget(engine, 'initialization:')
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        engine.store.reserve_daily(engine.policy, 1000, initialization_seed=seed)
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        engine.store.reserve_daily(engine.policy, 1000, initialization_seed=(seed[0], 'different-version'))
    assert budget(engine, 'initialization:') == spent


def test_related_old_memory_does_not_grant_an_ordinary_seed_initialization_exemption(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    initial = next(iter(engine.backend.records.values()))
    old = engine.backend.add('已有稳定偏好，仅作为相关上下文。', user_id='u1', scope=MemoryScope.SHARED,
        persona_id=None, metadata={'source_type': 'manual'})[0]
    service = attached_service(tmp_path, engine)
    model = ObservationModel()
    organizer = JournalOrganization(service.backend, service.operations.organization, model)
    batch = [service.backend.get(initial.id), service.backend.get(old.id)]
    organizer._call(batch, initialization_seed=(initial.id, memory_version(initial)))
    assert len(model.batches) == 1 and budget(engine, 'initialization:') > 0
    with pytest.raises(JournalMemoryError, match='journal_daily_budget'):
        organizer._call(batch, initialization_seed=(old.id, memory_version(old)))
    assert len(model.batches) == 1 and budget(engine, 'day:') == 0


def test_organization_failure_is_terminal_for_free_send_and_preserves_evidence_success(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    service = attached_service(tmp_path, engine)
    class BadOrganization:
        def complete(self, messages, tools):
            return AssistantTurn('invalid structure')
    service.operations.organization.request('u1')
    organizer = MemoryOrganizer(service.backend, service.operations.organization, BadOrganization())
    assert organizer.process_next()
    assert engine.initialization_status()['state'] == 'blocked'
    assert engine.initialization_status()['completed'] == 1
    assert engine.initialization_status()['organization'] == {'failed': 1}


def test_missing_backend_result_does_not_keep_an_initial_batch_alive(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    engine.store.execute('DELETE FROM initialization_seeds')
    engine.backend.records.clear()
    engine.advance_initialization()
    assert engine.initialization_status()['state'] == 'completed'


def test_observation_output_is_excluded_from_organization_and_does_not_leave_an_active_batch(tmp_path):
    engine, model = initialized(tmp_path, organization=True)
    original = model.complete
    def observation(messages, tools):
        result = original(messages, tools)
        payload = json.loads(result.content)
        for item in payload.get('memories', []):
            item['kind'] = 'observation'
        return AssistantTurn(json.dumps(payload, ensure_ascii=False))
    model.complete = observation
    engine.process_next()
    service = attached_service(tmp_path, engine)
    organizer_model = ObservationModel()
    organizer = MemoryOrganizer(service.backend, service.operations.organization, organizer_model)
    service.operations.organization.request('u1')
    assert organizer.process_next()
    assert not organizer_model.batches
    assert engine.initialization_status()['state'] == 'completed'
    assert engine.initialization_status()['organization'] == {'excluded': 1}
    assert not organizer.process_next()


def test_forgetting_removes_initialization_group_text_without_restoring_free_allowance(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    service = attached_service(tmp_path, engine)
    service.operations.organization.request('u1')
    assert MemoryOrganizer(service.backend, service.operations.organization, ObservationModel()).process_next()
    assert engine.store.rows('SELECT report_json FROM initialization_seeds')[0]['report_json']
    service.delete_memory('m1', user_id='u1')
    seed = engine.store.rows('SELECT state,report_json FROM initialization_seeds')[0]
    assert seed == {'state': 'done', 'report_json': None}
    assert not engine.initialization_status()['active']


def test_late_initialization_report_cannot_reintroduce_forgotten_context(tmp_path):
    engine, _ = initialized(tmp_path, organization=True)
    engine.process_next()
    seed = next(iter(engine.backend.records.values()))
    engine.store.reserve_daily(engine.policy, 1000, initialization_seed=(seed.id, memory_version(seed)))
    engine.store.suppress(seed.id, content_key(seed.content))
    report = {'versions': {seed.id: memory_version(seed)}, 'summary': 'Synthetic late result must be discarded.'}
    engine.store.finish_initialization_seed(seed.id, memory_version(seed), 'done', report)
    assert engine.store.rows('SELECT state,report_json FROM initialization_seeds')[0] == {'state': 'done', 'report_json': None}


def test_organization_budget_wake_is_limited_to_the_requested_owner_and_reason(tmp_path):
    engine, _ = initialized(tmp_path)
    service = attached_service(tmp_path, engine)
    store = service.operations.organization
    for owner in ('u1', 'u2'):
        store.request(owner)
    store._conn.execute("UPDATE memory_organization_runs SET error_code='journal_daily_budget',available_at='2999-01-01'")
    store._conn.commit()
    store.wake_daily_budget('u1')
    rows = store._conn.execute('SELECT user_id,error_code,available_at FROM memory_organization_runs ORDER BY user_id').fetchall()
    assert rows[0]['error_code'] is None and rows[0]['available_at'] < '2999'
    assert rows[1]['error_code'] == 'journal_daily_budget' and rows[1]['available_at'] == '2999-01-01'
