"""Member-only persistent groups exercise the real worker, grants and receipts."""

import json

import pytest

from test_mobile_api import drain, mentor_runtime, mobile, post
from riji_agent.mentors.models import Application, Artifact, AudienceGrant, Conversation, Delivery, Source, TransportResult
from riji_agent.mentors.store import key

MEMBERS = ['gentle_reviewer', 'future_self']


def create_group(mobile, operation='create-group', members=None):
    _, client, _, _ = mobile
    result = post(client, 'roundtables', operation_id=operation, chat_kind='group',
                  personas=MEMBERS if members is None else members, text='')
    assert result['state'] == 'completed', result
    return result['result']['conversation_id']


def detail(mobile, identifier):
    return mobile[1].get('/mobile/v1/roundtables/' + identifier).json()


def send_group(mobile, identifier, operation='group-message', **extra):
    _, client, _, _ = mobile
    return post(client, 'roundtables/' + identifier + '/messages', operation_id=operation,
                expected_revision=detail(mobile, identifier)['revision'], text='Synthetic group question', **extra)


def test_empty_creation_is_persistent_member_only_and_calls_no_model(mobile, monkeypatch):
    service, client, provider, paired = mobile
    def forbidden(*args, **kwargs):
        pytest.fail('Group creation and chat must not retrieve personal material')
    monkeypatch.setattr(service.runtime.service.policy.sources, 'background', forbidden)
    identifier = create_group(mobile)
    view = detail(mobile, identifier)
    assert view['chat_kind'] == 'group' and view['status'] == 'completed'
    assert view['messages'] == [] and view['audience'] == MEMBERS and view['personas'] == MEMBERS
    assert not provider.calls and not service.runtime.worker.run_one(identifier)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    grant = service.runtime.store.read('grant', current.grant_id, AudienceGrant)
    apps = {item.id: item for item in service.runtime.store.list('application', '', Application)}
    assert {apps[item].persona_id for item in grant.snapshot.application_ids} == set(MEMBERS)
    assert current.source_ids == () and current.history_artifact_ids == ()
    listing = client.get('/mobile/v1/roundtables').json()
    assert listing['items'][0]['id'] == identifier and listing['items'][0]['personas'] == MEMBERS
    assert listing['items'][0]['title'] == view['title']
    assert post(client, 'roundtables', operation_id='create-group', chat_kind='group', personas=MEMBERS, text='')['result']['conversation_id'] == identifier
    assert client.get('/mobile/v1/capabilities').json()['capabilities']['group_chat'] is True


def test_each_member_answers_once_with_no_host_or_comparison(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    assert send_group(mobile, identifier)['state'] == 'completed'
    drain(service, identifier)
    view = detail(mobile, identifier)
    assert view['status'] == 'completed'
    assert [item['actor'] for item in view['messages']] == ['user', *MEMBERS]
    assert len(provider.calls) == 2
    assert all(item['kind'] == 'followup' for item in view['messages'][1:])
    assert not any(item['actor'] == 'host' for item in view['messages'])


def test_mention_changes_only_responders_and_next_message_reaches_all(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    assert send_group(mobile, identifier, reply_personas=['future_self'])['state'] == 'completed'
    drain(service, identifier)
    view = detail(mobile, identifier)
    assert view['audience'] == MEMBERS and view['run_personas'] == ['future_self']
    assert [item['actor'] for item in view['messages']] == ['user', 'future_self']
    assert send_group(mobile, identifier, operation='next-message')['state'] == 'completed'
    drain(service, identifier)
    assert len(provider.calls) == 3
    assert detail(mobile, identifier)['run_personas'] == MEMBERS
    payload = json.loads(provider.calls[-2][0][-1]['content'])
    assert view['messages'][-1]['id'] in json.dumps(payload)


def test_groups_are_independent_of_private_chat_other_groups_and_titles(mobile):
    service, client, provider, _ = mobile
    private = post(client, 'mentors/gentle_reviewer/messages', operation_id='private', text='PRIVATE_SENTINEL')
    drain(service, private['result']['conversation_id'])
    first, second = create_group(mobile), create_group(mobile, operation='another-group')
    assert first != second and detail(mobile, first)['title'] == detail(mobile, second)['title']
    result = send_group(mobile, first)
    assert result['state'] == 'completed'
    drain(service, first)
    assert 'PRIVATE_SENTINEL' not in json.dumps(provider.calls[1:])
    assert not detail(mobile, second)['messages']
    view = detail(mobile, first)
    renamed = post(client, 'roundtables/' + first + '/commands', operation_id='rename', action='rename',
                   expected_revision=view['revision'], text='Ongoing synthetic group')
    assert renamed['state'] == 'completed'
    actual = detail(mobile, first)
    assert actual['title'] == 'Ongoing synthetic group' and actual['messages'] == view['messages']
    assert actual['personas'] == MEMBERS


@pytest.mark.parametrize('members', [[], ['future_self'], ['future_self', 'future_self'], ['host', 'future_self'], ['unknown', 'future_self']])
def test_invalid_members_create_no_group(mobile, members):
    _, client, provider, _ = mobile
    response = client.post('/mobile/v1/roundtables', json=dict(operation_id='invalid', chat_kind='group', personas=members))
    assert response.status_code == 200 and response.json()['state'] == 'failed'
    assert client.get('/mobile/v1/roundtables').json()['items'] == [] and not provider.calls


@pytest.mark.parametrize('members', [['host'], ['blunt_coach'], ['future_self', 'future_self']])
def test_mentions_cannot_add_or_duplicate_audience(mobile, members):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    assert send_group(mobile, identifier, reply_personas=members)['state'] == 'failed'
    assert detail(mobile, identifier)['messages'] == []
    assert not provider.calls and service.runtime.store.list('artifact', identifier, Artifact) == ()


def test_archive_restore_empty_group_does_not_generate_or_change_members(mobile):
    service, client, provider, _ = mobile
    identifier = create_group(mobile)
    for action in ['archive', 'restore']:
        view = detail(mobile, identifier)
        receipt = post(client, 'roundtables/' + identifier + '/commands', operation_id=action,
                       action=action, expected_revision=view['revision'])
        assert receipt['state'] == 'completed'
        assert post(client, 'roundtables/' + identifier + '/commands', operation_id=action,
                    action=action, expected_revision=view['revision']) == receipt
    assert detail(mobile, identifier)['status'] == 'stopped' and not provider.calls
    assert not service.runtime.worker.run_one(identifier)
    assert send_group(mobile, identifier)['state'] == 'completed'
    drain(service, identifier)
    assert [item['actor'] for item in detail(mobile, identifier)['messages']] == ['user', *MEMBERS]


def test_stop_resume_completes_only_remaining_selected_member(mobile):
    service, client, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    service.runtime.worker.dispatcher.dispatch_one(identifier)
    view = detail(mobile, identifier)
    post(client, 'roundtables/' + identifier + '/commands', operation_id='stop', action='stop', expected_revision=view['revision'])
    post(client, 'roundtables/' + identifier + '/commands', operation_id='resume', action='resume', expected_revision=view['revision'])
    drain(service, identifier)
    assert len(provider.calls) == 2
    assert [item['actor'] for item in detail(mobile, identifier)['messages']] == ['user', *MEMBERS]


def test_group_unknown_operation_does_not_repeat_durable_message(mobile, monkeypatch):
    service, client, _, _ = mobile
    identifier = create_group(mobile)
    def lost_ack():
        raise RuntimeError('Synthetic lost acknowledgement')
    monkeypatch.setattr(service.runtime.worker, 'wake', lost_ack)
    receipt = send_group(mobile, identifier)
    assert receipt['state'] == 'unknown'
    replay = post(client, 'roundtables/' + identifier + '/messages', operation_id='group-message',
                  expected_revision=1, text='Synthetic group question')
    assert replay == receipt
    assert len(service.runtime.store.list('artifact', identifier, Artifact)) == 1


def test_group_and_grant_cannot_include_unselected_or_foreign_device(mobile):
    service, client, provider, paired = mobile
    identifier = create_group(mobile)
    other = service.store.provision(next(iter(service.runtime.user_tokens.values())), 'Other synthetic device')
    client.headers['Authorization'] = 'Bearer ' + other['device_token']
    assert client.get('/mobile/v1/roundtables/' + identifier).status_code == 404
    client.headers['Authorization'] = 'Bearer ' + paired['device_token']
    send_group(mobile, identifier)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    grant = service.runtime.store.read('grant', current.grant_id, AudienceGrant)
    host = next(item for item in service.runtime.store.list('application', '', Application) if item.platform == 'mobile' and item.role == 'host')
    snapshot = grant.snapshot.model_copy(update={'application_ids': (*grant.snapshot.application_ids, host.id)})
    with service.runtime.store.transaction() as db:
        service.runtime.store.put(db, 'mobile_room', snapshot, current.id, current.room_id)
        service.runtime.store.put(db, 'grant', grant.model_copy(update={'snapshot': snapshot}), current.owner_id)
    assert client.get('/mobile/v1/roundtables/' + identifier).status_code == 409
    drain(service, identifier)
    assert not provider.calls


def test_group_cannot_start_legacy_materials_or_hidden_host_actions(mobile):
    _, client, provider, _ = mobile
    identifier = create_group(mobile)
    for action in ['start_run', 'summarize', 'continue', 'correct', 'resume']:
        result = post(client, 'roundtables/' + identifier + '/commands', operation_id='reject-' + action,
                      action=action, expected_revision=1)
        assert result['state'] == 'failed'
    assert not provider.calls and detail(mobile, identifier)['messages'] == []


def test_group_creation_lost_receipt_recovers_committed_proof_and_creates_only_once(mobile, monkeypatch):
    service, client, _, _ = mobile
    finish = service.store.finish
    def lost_receipt(*args, **kwargs):
        raise RuntimeError('Synthetic response interruption')
    monkeypatch.setattr(service.store, 'finish', lost_receipt)
    body = dict(operation_id='lost-create', chat_kind='group', personas=MEMBERS)
    assert client.post('/mobile/v1/roundtables', json=body).status_code == 503
    monkeypatch.setattr(service.store, 'finish', finish)
    service.store.recover()
    receipt = client.get('/mobile/v1/operations/lost-create').json()
    assert receipt['state'] == 'completed'
    assert len(client.get('/mobile/v1/roundtables').json()['items']) == 1
    assert post(client, 'roundtables', **body) == receipt


def test_group_stale_revision_preserves_message_history(mobile):
    service, client, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    drain(service, identifier)
    receipt = post(client, 'roundtables/' + identifier + '/messages', operation_id='stale',
                   expected_revision=1, text='Stale synthetic message')
    assert receipt['state'] == 'failed' and receipt['error'] == 'input_changed'
    assert len(detail(mobile, identifier)['messages']) == 3 and len(provider.calls) == 2


def test_long_group_keeps_complete_history_with_bounded_live_context(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    with service.runtime.store.transaction() as db:
        for number in range(60):
            service.runtime.service._user_artifact(db, current, 'Old synthetic message ' + str(number))
    assert send_group(mobile, identifier)['state'] == 'completed'
    drain(service, identifier)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    assert current.status == 'completed' and current.summary_status == 'current'
    assert len(service.runtime.store.list('artifact', identifier, Artifact)) == 63
    payload = json.loads(provider.calls[-1][0][-1]['content'])
    assert len(payload['working_summary']['items']) <= 48 and len(payload['previous']) <= 24
    assert 'Old synthetic message 0"' not in json.dumps(payload)
    assert payload['question'] == 'Synthetic group question'


def test_group_device_revocation_blocks_queued_models(mobile):
    service, _, provider, paired = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.store.revoke(paired['device_id'])
    drain(service, identifier)
    assert not provider.calls
    assert service.runtime.store.read('conversation', identifier, Conversation).status == 'interrupted'


def test_mention_does_not_narrow_source_permission_to_only_the_responder(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    source = Source(id='synthetic-source', owner_id=current.owner_id, version='one', text='Restricted synthetic material',
                    kind='memory', allowed_personas=('future_self',))
    source_key = key(current.owner_id, source.id, source.version)
    grant = service.runtime.store.read('grant', current.grant_id, AudienceGrant)
    with service.runtime.store.transaction() as db:
        service.runtime.store.put(db, 'source', source, current.owner_id, source_key)
        service.runtime.store.put(db, 'conversation', current.model_copy(update={'source_ids': (source_key,)}), current.owner_id)
        service.runtime.store.put(db, 'grant', grant.model_copy(update={'source_versions': {source.id: source.version}}), current.owner_id)
    assert send_group(mobile, identifier, reply_personas=['future_self'])['state'] == 'completed'
    drain(service, identifier)
    assert not provider.calls
    assert service.runtime.store.read('conversation', identifier, Conversation).status == 'interrupted'


def test_legacy_receipt_fingerprint_does_not_gain_new_optional_fields(mobile):
    service, client, _, paired = mobile
    body = dict(operation_id='legacy-pending', text='Synthetic legacy question', statement_kind='user_statement', conversation_id='')
    created, receipt = service.store.begin(paired['device_id'], body['operation_id'], 'roundtable_create', body)
    assert created
    assert post(client, 'roundtables', operation_id=body['operation_id'], text=body['text']) == receipt


def test_rename_advances_revision_rejects_stale_name_and_preserves_provenance(mobile):
    service, client, _, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    drain(service, identifier)
    view = detail(mobile, identifier)
    first = post(client, 'roundtables/' + identifier + '/commands', operation_id='rename-first',
                 action='rename', expected_revision=view['revision'], text='Synthetic renamed group')
    assert first['state'] == 'completed'
    renamed = detail(mobile, identifier)
    assert renamed['revision'] == view['revision'] + 1
    assert renamed['messages'] == view['messages'] and renamed['run_id'] == view['run_id']
    assert renamed['runs'][-1]['input_revision'] == view['runs'][-1]['input_revision']
    stale = post(client, 'roundtables/' + identifier + '/commands', operation_id='rename-stale',
                 action='rename', expected_revision=view['revision'], text='Stale synthetic group name')
    assert stale['state'] == 'failed' and stale['error'] == 'input_changed'
    assert detail(mobile, identifier)['title'] == renamed['title']


@pytest.mark.parametrize('action', ['rename', 'archive'])
def test_running_group_cannot_be_renamed_or_silently_archived(mobile, action):
    service, client, _, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    before = detail(mobile, identifier)
    deliveries = service.runtime.store.list('delivery', identifier, Delivery)
    result = post(client, 'roundtables/' + identifier + '/commands', operation_id='active-' + action,
                  action=action, expected_revision=before['revision'], text='Synthetic name' if action == 'rename' else '')
    assert result['state'] == 'failed' and result['error'] == 'discussion_still_running'
    assert service.runtime.store.list('delivery', identifier, Delivery) == deliveries
    assert detail(mobile, identifier)['revision'] == before['revision']
    drain(service, identifier)
    assert len(detail(mobile, identifier)['messages']) == 3


@pytest.mark.parametrize('management', ['none', 'rename', 'archive-restore'])
def test_stop_before_delivery_resumes_same_reply_without_model_duplication(mobile, management):
    service, client, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    generated = service.runtime.store.list('artifact', identifier, Artifact)[-1]
    delivery = service.runtime.store.list('delivery', identifier, Delivery)[-1]
    assert delivery.status == 'pending' and delivery.attempts == 0
    view = detail(mobile, identifier)
    post(client, 'roundtables/' + identifier + '/commands', operation_id='stop-pending', action='stop', expected_revision=view['revision'])
    actions = ['rename'] if management == 'rename' else ['archive', 'restore'] if management == 'archive-restore' else []
    for action in actions:
        view = detail(mobile, identifier)
        result = post(client, 'roundtables/' + identifier + '/commands', operation_id='manage-' + action, action=action,
                      expected_revision=view['revision'], text='Synthetic revised name' if action == 'rename' else '')
        assert result['state'] == 'completed'
    view = detail(mobile, identifier)
    result = post(client, 'roundtables/' + identifier + '/commands', operation_id='resume-pending', action='resume', expected_revision=view['revision'])
    assert result['state'] == 'completed'
    restored = service.runtime.store.read('delivery', delivery.id, Delivery)
    assert restored.status == 'pending' and restored.attempts == 0
    drain(service, identifier)
    view = detail(mobile, identifier)
    assert view['status'] == 'completed' and len(provider.calls) == 2
    assert [item['actor'] for item in view['messages']] == ['user', *MEMBERS]
    assert any(item['id'] == generated.id for item in view['messages'])
    assert service.runtime.store.read('artifact', generated.id, Artifact) == generated


def test_stop_during_delivery_requires_reconciliation_and_never_resends_unknown(mobile):
    service, client, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    sending = service.runtime.worker.dispatcher._claim(identifier)
    assert sending.status == 'sending'
    view = detail(mobile, identifier)
    post(client, 'roundtables/' + identifier + '/commands', operation_id='stop-sending', action='stop', expected_revision=view['revision'])
    assert service.runtime.store.read('delivery', sending.id, Delivery).status == 'unknown'
    for action in ['resume', 'archive', 'rename']:
        result = post(client, 'roundtables/' + identifier + '/commands', operation_id='unknown-' + action,
                      action=action, expected_revision=view['revision'], text='Synthetic name' if action == 'rename' else '')
        assert result['state'] == 'failed' and result['error'] in {'step_reconciliation_required', 'delivery_reconciliation_required'}
    assert len(provider.calls) == 1
    service.runtime.worker.dispatcher._record(sending, TransportResult(status='sent', message_id='synthetic-reconciled-receipt'))
    result = post(client, 'roundtables/' + identifier + '/commands', operation_id='resume-reconciled', action='resume', expected_revision=view['revision'])
    assert result['state'] == 'completed'
    drain(service, identifier)
    assert len(provider.calls) == 2
    assert [item['actor'] for item in detail(mobile, identifier)['messages']] == ['user', *MEMBERS]
    assert service.runtime.store.read('delivery', sending.id, Delivery).attempts == 1
