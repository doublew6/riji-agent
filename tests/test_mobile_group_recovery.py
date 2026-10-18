"""Durable provenance, delivery visibility and honest recovery for mobile groups."""

import json
import sqlite3

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_mobile_api import drain, mentor_runtime, mobile, post
from test_mobile_group_chats import MEMBERS, create_group, detail, send_group
from riji_agent.mentors.models import Artifact, Conversation, Delivery, RoomSnapshot, SummaryItem, TransportResult


def command(mobile, identifier, action, operation=None, **fields):
    return post(mobile[1], 'roundtables/' + identifier + '/commands', operation_id=operation or action,
                action=action, expected_revision=detail(mobile, identifier)['revision'], **fields)


def test_old_group_result_keeps_its_original_user_topic_after_subject_changes(mobile):
    service, client, _, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    drain(service, identifier)
    original = detail(mobile, identifier)
    chosen = original['messages'][1]['id']
    post(client, 'roundtables/' + identifier + '/messages', operation_id='different-topic',
         expected_revision=original['revision'], text='A completely different synthetic topic')
    drain(service, identifier)
    selected = post(client, 'roundtables/' + identifier + '/result-preview', operation_id='old-result', artifact_ids=[chosen])
    validated = post(client, 'roundtables/' + identifier + '/result-validation', result_id=selected['result']['result_id'])
    assert validated['provenance']['topic'] == 'Synthetic group question'
    assert validated['provenance']['discussion_id'] == original['run_id']
    assert validated['provenance']['artifact_ids'] == [chosen]


def test_group_summary_retains_each_speaker_with_old_schema_compatibility(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    drain(service, identifier)
    send_group(mobile, identifier, operation='follow-speakers')
    drain(service, identifier)
    payload = json.loads(provider.calls[2][0][-1]['content'])
    assert {item['actor'] for item in payload['working_summary']['items']} == {'user', *MEMBERS}
    legacy = dict(payload['working_summary']['items'][0])
    legacy.pop('actor')
    assert SummaryItem.model_validate(legacy).actor == ''


def test_existing_delete_api_erases_group_title_and_mobile_room(mobile):
    from riji_agent.mentors.api import build_router
    service, client, _, _ = mobile
    identifier = create_group(mobile)
    assert command(mobile, identifier, 'rename', text='PRIVATE_GROUP_TITLE_SENTINEL')['state'] == 'completed'
    current = service.runtime.store.read('conversation', identifier, Conversation)
    assert service.runtime.store.read('mobile_room', current.room_id, RoomSnapshot) is not None
    app = FastAPI()
    app.include_router(build_router(service.runtime))
    owner = TestClient(app)
    owner.headers['Authorization'] = 'Bearer ' + next(iter(service.runtime.user_tokens))
    result = owner.post('/api/mentors/v1/conversations/' + identifier + '/commands', json={'id': 'delete-group', 'kind': 'delete'})
    assert result.status_code == 200
    deleted = service.runtime.store.read('conversation', identifier, Conversation)
    assert deleted.status == 'deleted' and deleted.title == '' and deleted.question == '[deleted]'
    assert service.runtime.store.read('mobile_room', current.room_id, RoomSnapshot) is None
    assert not client.get('/mobile/v1/roundtables').json()['items']
    assert b'PRIVATE_GROUP_TITLE_SENTINEL' not in service.runtime.store.path.read_bytes()


def test_unseen_stopped_reply_is_excluded_when_user_sends_a_new_message(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    unseen = service.runtime.store.list('artifact', identifier, Artifact)[-1]
    assert command(mobile, identifier, 'stop')['state'] == 'completed'
    assert send_group(mobile, identifier, operation='new-message')['state'] == 'completed'
    drain(service, identifier)
    first_new_payload = json.loads(provider.calls[1][0][-1]['content'])
    for context in [first_new_payload['previous'], first_new_payload['working_summary']]:
        assert unseen.id not in json.dumps(context) and unseen.text not in json.dumps(context)
    assert unseen.id not in [item['id'] for item in detail(mobile, identifier)['messages']]


def test_legacy_group_creation_without_version_or_proof_remains_unknown(mobile):
    service, client, provider, paired = mobile
    body = {'operation_id': 'no-proof', 'chat_kind': 'group', 'personas': MEMBERS,
            'text': '', 'statement_kind': 'user_statement', 'conversation_id': ''}
    service.store.begin(paired['device_id'], body['operation_id'], 'roundtable_create', body)
    with service.store.transaction() as db:
        db.execute('DELETE FROM operation_protocols WHERE device=? AND id=?', (paired['device_id'], body['operation_id']))
    service.store.recover()
    assert client.get('/mobile/v1/operations/no-proof').json()['state'] == 'unknown'
    body.pop('conversation_id')
    assert post(client, 'roundtables', **body)['state'] == 'unknown'
    assert not client.get('/mobile/v1/roundtables').json()['items'] and not provider.calls


@pytest.mark.parametrize('point', ['before', 'during'])
def test_v2_creation_crash_before_commit_proves_no_group_and_allows_fresh_create(mobile, monkeypatch, point):
    import riji_agent.mobile.group_chats as groups
    service, client, provider, _ = mobile
    original_apps, original_bind = groups._applications, service.runtime.store.bind
    def before(*args):
        raise RuntimeError('Synthetic precommit interruption')
    def during(db, kind, identifier, value):
        if kind == 'mobile_group_creation':
            raise RuntimeError('Synthetic transaction interruption')
        return original_bind(db, kind, identifier, value)
    monkeypatch.setattr(groups, '_applications', before if point == 'before' else original_apps)
    monkeypatch.setattr(service.runtime.store, 'bind', during if point == 'during' else original_bind)
    body = {'operation_id': 'crash-v2', 'chat_kind': 'group', 'personas': MEMBERS}
    assert post(client, 'roundtables', **body)['state'] == 'unknown'
    checked = client.get('/mobile/v1/operations/crash-v2').json()
    assert checked['state'] == 'failed' and checked['error'] == 'group_creation_not_committed'
    with service.runtime.store.transaction() as db:
        assert not db.execute("SELECT 1 FROM mentor_records WHERE kind IN ('conversation','grant','mobile_room')").fetchone()
    assert post(client, 'roundtables', **body) == checked
    monkeypatch.setattr(groups, '_applications', original_apps)
    monkeypatch.setattr(service.runtime.store, 'bind', original_bind)
    identifier = create_group(mobile, operation='fresh-after-failed')
    assert detail(mobile, identifier)['messages'] == [] and not provider.calls


def test_v2_current_pending_is_not_mistaken_for_a_crash(mobile):
    service, client, _, paired = mobile
    body = {'operation_id': 'active-create', 'chat_kind': 'group', 'personas': MEMBERS,
            'text': '', 'statement_kind': 'user_statement', 'conversation_id': ''}
    service.store.begin(paired['device_id'], body['operation_id'], 'roundtable_create', body)
    checked = client.get('/mobile/v1/operations/active-create').json()
    assert checked['state'] == 'pending' and checked['error'] is None


def test_v2_recovery_failed_guard_rejects_late_creation_commit(mobile, monkeypatch):
    service, client, provider, paired = mobile
    adapter = service.runtime.service.policy.channel.adapters['mobile']
    prepare = adapter.prepare_room
    def recovered_before_commit(*args):
        snapshot = prepare(*args)
        service.store.recover()
        assert service.store.fail_uncommitted_group(paired['device_id'], 'late-create')['state'] == 'failed'
        return snapshot
    monkeypatch.setattr(adapter, 'prepare_room', recovered_before_commit)
    receipt = post(client, 'roundtables', operation_id='late-create', chat_kind='group', personas=MEMBERS)
    assert receipt['state'] == 'failed'
    assert not client.get('/mobile/v1/roundtables').json()['items'] and not provider.calls
    with service.runtime.store.transaction() as db:
        assert not db.execute("SELECT 1 FROM mentor_records WHERE kind IN ('conversation','grant','mobile_room')").fetchone()


def test_v2_atomic_creation_refuses_wal_instead_of_changing_storage_mode(mobile):
    service, client, provider, _ = mobile
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    receipt = post(client, 'roundtables', operation_id='wal-create', chat_kind='group', personas=MEMBERS)
    assert receipt['state'] == 'failed' and receipt['error'] == 'mobile_storage_unavailable'
    assert not client.get('/mobile/v1/roundtables').json()['items'] and not provider.calls
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_unknown_model_is_ended_explicitly_with_audit_preserved_and_new_turn_allowed(mobile, monkeypatch):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    generator = service.runtime.worker.generator
    generate, uncertain_calls = generator.generate, []
    def uncertain(request, before_send):
        before_send()
        uncertain_calls.append(request)
        raise TimeoutError('Synthetic uncertain model response')
    monkeypatch.setattr(generator, 'generate', uncertain)
    send_group(mobile, identifier)
    drain(service, identifier)
    old_run = detail(mobile, identifier)['run_id']
    assert command(mobile, identifier, 'resume', operation='unsafe-resume')['state'] == 'failed'
    assert command(mobile, identifier, 'end_turn')['state'] == 'completed'
    assert detail(mobile, identifier)['turn_ended'] is True
    assert command(mobile, identifier, 'resume', operation='ended-resume')['error'] == 'group_turn_ended'
    with service.runtime.store.transaction() as db:
        rows = db.execute("SELECT key,status FROM mentor_steps WHERE conversation_id=?", (identifier,)).fetchall()
    assert len(rows) == 1 and rows[0]['status'] == 'unknown' and json.loads(rows[0]['key'])[0] == old_run
    monkeypatch.setattr(generator, 'generate', generate)
    assert send_group(mobile, identifier, operation='after-end')['state'] == 'completed'
    drain(service, identifier)
    assert len(uncertain_calls) == 1 and len(provider.calls) == 2
    assert detail(mobile, identifier)['status'] == 'completed' and detail(mobile, identifier)['turn_ended'] is False


def test_late_old_delivery_receipt_cannot_interrupt_new_turn_or_duplicate_reply(mobile):
    service, _, provider, _ = mobile
    identifier = create_group(mobile)
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    sending = service.runtime.worker.dispatcher._claim(identifier)
    service.runtime.worker.dispatcher._record(sending, TransportResult(status='unknown'))
    assert command(mobile, identifier, 'end_turn')['state'] == 'completed'
    assert send_group(mobile, identifier, operation='new-after-unknown')['state'] == 'completed'
    before = service.runtime.store.read('conversation', identifier, Conversation)
    service.runtime.worker.dispatcher._record(sending, TransportResult(status='unknown'))
    assert service.runtime.store.read('conversation', identifier, Conversation) == before
    drain(service, identifier)
    for call in provider.calls[1:]:
        assert sending.artifact_id not in json.dumps(json.loads(call[0][-1]['content']))
    service.runtime.worker.dispatcher._record(sending, TransportResult(status='sent', message_id='late-receipt'))
    view = detail(mobile, identifier)
    assert view['status'] == 'completed' and len(provider.calls) == 3
    assert sum(item['id'] == sending.artifact_id for item in view['messages']) == 1
    assert view['last_reply_id'] == sending.artifact_id
    service.runtime.worker.dispatcher._record(sending, TransportResult(status='unknown'))
    assert service.runtime.store.read('delivery', sending.id, Delivery).status == 'sent'


def test_reply_cursors_use_only_sent_ai_and_do_not_change_on_rename(mobile):
    service, client, _, _ = mobile
    identifier = create_group(mobile)
    assert detail(mobile, identifier)['last_reply_id'] == ''
    send_group(mobile, identifier)
    service.runtime.worker.run_one(identifier)
    assert detail(mobile, identifier)['last_reply_id'] == ''
    drain(service, identifier)
    last = detail(mobile, identifier)['messages'][-1]['id']
    assert detail(mobile, identifier)['last_reply_id'] == last
    assert client.get('/mobile/v1/roundtables').json()['items'][0]['last_reply_id'] == last
    command(mobile, identifier, 'rename', text='Synthetic cursor-preserving name')
    assert detail(mobile, identifier)['last_reply_id'] == last
    private = post(client, 'mentors/future_self/messages', operation_id='private-cursor', text='Synthetic private message')
    drain(service, private['result']['conversation_id'])
    private_view = client.get('/mobile/v1/mentors/future_self/conversation').json()
    assert private_view['last_reply_id'] == private_view['messages'][-1]['id']
    summary = next(item for item in client.get('/mobile/v1/capabilities').json()['mentors'] if item['id'] == 'future_self')
    assert summary['last_reply_id'] == private_view['last_reply_id']
