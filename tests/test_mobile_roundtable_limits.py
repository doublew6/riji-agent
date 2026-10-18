"""Bounded ten-round mobile runs and their original source/receipt guards."""
import pytest

from riji_agent.mentors.budget import BudgetService
from riji_agent.mentors.models import Conversation, MentorError, Source
from test_mentor_discussions import SyntheticGenerator
from test_mobile_api import mobile, mentor_runtime, post
from test_mobile_roundtables import create


def preview_run(mobile, rounds=10, mode="debate"):
    service, client, _, _ = mobile
    identifier = create(mobile)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    body = dict(expected_revision=view['revision'],
                artifact_ids=[item['id'] for item in view['messages'] if item['kind'] == 'user'],
                memories=[], personas=['gentle_reviewer', 'blunt_coach', 'future_self', 'wang_yangming'],
                mode=mode, rounds=rounds)
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    command = dict(operation_id='ten-round-invitation', action='start_run',
                   expected_revision=view['revision'], personas=body['personas'], mode=mode,
                   rounds=rounds, preview_hash=preview['preview_hash'])
    return identifier, command


def test_four_mentor_ten_round_run_keeps_initial_evidence_and_finishes(mobile):
    service, client, _, _ = mobile
    identifier, command = preview_run(mobile)
    generator = SyntheticGenerator()
    service.runtime.worker.generator = generator
    receipt = post(client, 'roundtables/' + identifier + '/commands', **command)
    assert receipt['state'] == 'completed'
    assert post(client, 'roundtables/' + identifier + '/commands', **command) == receipt
    for _ in range(100):
        worked = service.runtime.worker.run_one(identifier)
        sent = service.runtime.worker.dispatcher.dispatch_one(identifier)
        if not worked and not sent:
            break
    else:
        pytest.fail('Ten-round synthetic run did not terminate')
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert view['status'] == 'completed'
    assert view['rounds'] == 10 and len(generator.calls) == 46
    assert generator.calls[-1].stage == 'synthesis'
    assert all(not call.previous for call in generator.calls if call.stage == 'opinion')
    for persona in command['personas']:
        calls = [call for call in generator.calls if call.actor == persona and call.stage == 'debate']
        assert [call.round_index for call in calls] == list(range(1, 11))
    for call in generator.calls:
        assert len(call.previous) <= 24
        if call.stage in {'debate', 'synthesis'}:
            assert len([item for item in call.previous if item.kind == 'opinion']) == 4
            assert len([item for item in call.previous if item.kind == 'comparison']) == 1
            assert all(item.run_id == call.conversation.run_id for item in call.previous)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    budget = service.runtime.service.budgets.status(current)['current']
    assert (budget['requests'], budget['request_limit'], budget['time_limit']) == (46, 92, 2520)


@pytest.mark.parametrize('rounds', [0, 11])
def test_mobile_invitation_rejects_out_of_range_rounds(mobile, rounds):
    _, client, _, _ = mobile
    identifier, command = preview_run(mobile)
    assert client.post('/mobile/v1/roundtables/' + identifier + '/commands',
                       json=dict(command, rounds=rounds)).status_code == 422
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert client.post('/mobile/v1/roundtables/' + identifier + '/materials/preview', json={
        'expected_revision': view['revision'], 'artifact_ids': [view['messages'][0]['id']],
        'memories': [], 'personas': command['personas'], 'mode': 'debate', 'rounds': rounds}).status_code == 422


def test_ten_round_preview_cannot_be_confirmed_with_different_rounds(mobile):
    _, client, _, _ = mobile
    identifier, command = preview_run(mobile)
    receipt = post(client, 'roundtables/' + identifier + '/commands', **dict(command, rounds=9))
    assert receipt['state'] == 'failed' and receipt['error'] == 'material_preview_changed'


@pytest.mark.parametrize('mode,rounds,requests,seconds', [
    ('reference', 10, 24, 600), ('debate', 1, 24, 600), ('debate', 2, 24, 600), ('debate', 10, 92, 2520)])
def test_run_budget_survives_recreation_and_time_limit_still_applies(mobile, mode, rounds, requests, seconds):
    service, client, _, _ = mobile
    identifier, command = preview_run(mobile, rounds, mode)
    post(client, 'roundtables/' + identifier + '/commands', **command)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    clock = [100.0]
    budgets = BudgetService(service.runtime.store, lambda: clock[0])
    with service.runtime.store.transaction() as db:
        budgets.pause(db, current)
        db.execute('UPDATE mentor_budgets SET elapsed=0 WHERE id=?', (current.run_id,))
        budgets.activate(db, current)
    budgets.charge(current, ())
    with service.runtime.store.transaction() as db:
        budgets.create(db, current)
    row = budgets.status(current)['current']
    assert (row['requests'], row['request_limit'], row['time_limit']) == (1, requests, seconds)
    clock[0] += seconds
    with pytest.raises(MentorError, match='budget_exhausted'):
        budgets.charge(current, ())


def test_ten_round_budget_keeps_source_egress_limit(mobile):
    service, client, _, _ = mobile
    identifier, command = preview_run(mobile)
    post(client, 'roundtables/' + identifier + '/commands', **command)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    source = Source(id='synthetic-source', version='v1', kind='journal', text='x' * 900,
                    owner_id=current.owner_id, allowed_personas=current.personas)
    budgets = service.runtime.service.budgets
    for _ in range(4):
        budgets.charge(current, (source,))
    with pytest.raises(MentorError, match='source_budget_exhausted'):
        budgets.charge(current, (source,))
