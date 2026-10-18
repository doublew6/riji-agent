"""Synthetic conversations run the production orchestration and delivery path."""
import json
import pytest
from test_mobile_api import mobile, mentor_runtime, post, drain
from riji_agent.mentors.models import Application, Artifact, Conversation, Delivery


def create(mobile):
    service, client, provider, device = mobile
    receipt = post(client, 'roundtables', operation_id='create-rt', text='Plan a small weekly learning experiment')
    assert receipt['state'] == 'completed', receipt
    identifier = receipt['result']['conversation_id']
    drain(service, identifier)
    return identifier


def test_host_only_and_three_relaunches(mobile):
    service, client, provider, _ = mobile
    identifier = create(mobile)
    assert len(provider.calls) == 1
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert view['status'] == 'completed', view
    assert [m['actor'] for m in view['messages']] == ['user', 'host']
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from riji_agent.mentors.runtime import build_runtime
    from riji_agent.mobile.api import build_router
    from riji_agent.mobile.service import MobileService
    from riji_agent.mobile.store import MobileStore
    root = service.runtime.store.path.parent.parent
    settings = SimpleNamespace(mentors_enabled=True, mentors_config_path=root / 'mentor-config.json',
        journal_root=root / 'vault', data_dir=root / 'data', port=8765, feishu_app_id=None)
    for _ in range(3):
        rebuilt = build_runtime(settings, model=provider, memory_service=service.memory.service, drafts=None)
        try:
            rebuilt_mobile = MobileService(MobileStore(service.store.path), rebuilt, service.memory.service)
            app = FastAPI(); app.include_router(build_router(rebuilt_mobile))
            restarted = TestClient(app); restarted.headers['Authorization'] = client.headers['Authorization']
            assert restarted.get('/mobile/v1/roundtables').json()['current_id'] == identifier
            actual = restarted.get('/mobile/v1/roundtables/' + identifier).json()
            assert actual['messages'] == view['messages']
            assert actual['status'] == view['status']
        finally:
            rebuilt.graph.close()
    apps = {a.id: a for a in service.runtime.store.list('application', '', Application)}
    assert all(apps[d.application_id].platform == 'mobile' for d in service.runtime.store.list('delivery', identifier, Delivery))
    assert service.runtime.service.get(identifier, view.get('owner_id', next(iter(service.runtime.user_tokens.values())))).source_ids == ()


def invite(mobile, identifier, mode="reference"):
    service, client, _, _ = mobile
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    body = dict(expected_revision=view['revision'], artifact_ids=[m['id'] for m in view['messages'] if m['kind'] == 'user'],
                memories=[], personas=['gentle_reviewer', 'blunt_coach'], mode=mode, rounds=1)
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    assert len(preview['audience']) == 5
    result = post(client, 'roundtables/' + identifier + '/commands', operation_id='invite', action='start_run',
                  expected_revision=view['revision'], personas=body['personas'], mode=mode, rounds=1,
                  preview_hash=preview['preview_hash'])
    assert result['state'] == 'completed', result
    return view


def test_invitation_deselection_and_local_result_receipt(mobile):
    service, client, provider, _ = mobile
    identifier = create(mobile)
    initial = invite(mobile, identifier)
    drain(service, identifier)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert view['status'] == 'completed', view
    assert len(provider.calls) >= 4
    assert initial['messages'][-1]['id'] not in json.dumps(provider.calls[1])
    chosen = [m['id'] for m in view['messages'] if m['kind'] == 'comparison']
    result = post(client, 'roundtables/' + identifier + '/result-preview', operation_id='save-preview', artifact_ids=chosen)
    assert result['state'] == 'completed', result
    assert set(result['result']) == {'result_id'}
    validation = post(client, 'roundtables/' + identifier + '/result-validation', result_id=result['result']['result_id'])
    assert validation['provenance']['artifact_ids'] == chosen
    post(client, 'roundtables/' + identifier + '/commands', operation_id='correction', action='correct', expected_revision=view['revision'],
         text='The available time is now only ten minutes', supersedes=[initial['messages'][0]['id']])
    assert client.post('/mobile/v1/roundtables/' + identifier + '/result-validation', json={'result_id': result['result']['result_id']}).status_code == 409
    assert post(client, 'roundtables/' + identifier + '/result-preview', operation_id='save-preview', artifact_ids=chosen)['result'] == result['result']


def test_device_and_foreign_conversation_denied(mobile):
    service, client, _, device = mobile
    identifier = create(mobile)
    other = service.store.provision(next(iter(service.runtime.user_tokens.values())), 'Other synthetic')
    client.headers['Authorization'] = 'Bearer ' + other['device_token']
    assert client.get('/mobile/v1/roundtables/' + identifier).status_code == 404
    assert client.get('/mobile/v1/roundtables').json()['items'] == []
    client.headers['Authorization'] = 'Bearer ' + device['device_token']
    private = post(client, 'mentors/gentle_reviewer/messages', operation_id='private', text='private')
    assert client.get('/mobile/v1/roundtables/' + private['result']['conversation_id']).status_code == 404
    service.store.revoke(device['device_id'])
    assert client.get('/mobile/v1/roundtables/' + identifier).status_code == 401


def test_no_automatic_memory_retrieval(mobile, monkeypatch):
    service, client, provider, _ = mobile
    def forbidden(*args, **kwargs):
        pytest.fail('Host chat must never perform implicit retrieval')
    monkeypatch.setattr(service.runtime.service.policy.sources, 'background', forbidden)
    identifier = create(mobile)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    result = post(client, 'roundtables/' + identifier + '/messages', operation_id='followup', text='Later feedback: tried once',
                  statement_kind='user_feedback', expected_revision=view['revision'])
    assert result['state'] == 'completed'
    drain(service, identifier)
    assert len(provider.calls) == 2
    assert client.get('/mobile/v1/roundtables/' + identifier).json()['messages'][-2]['origin_kind'] == 'user_feedback'


def test_rejected_start_rolls_back_materials_grant_summary(mobile, monkeypatch):
    from riji_agent.mentors.models import MentorError
    service, client, _, _ = mobile
    identifier = create(mobile)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    def reject(*args):
        raise MentorError('synthetic_budget_failure')
    monkeypatch.setattr(service.runtime.service.lifecycle, '_budget', reject)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    body = dict(expected_revision=view['revision'], artifact_ids=[view['messages'][0]['id']], memories=[],
                personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    with service.runtime.store.transaction() as db:
        before = list(map(tuple, db.execute('SELECT * FROM mentor_records ORDER BY kind,id')))
    result = post(client, 'roundtables/' + identifier + '/commands', operation_id='bad-start', action='start_run',
                  expected_revision=view['revision'], personas=body['personas'], mode='reference', rounds=1, preview_hash=preview['preview_hash'])
    assert result['state'] == 'failed'
    with service.runtime.store.transaction() as db:
        assert list(map(tuple, db.execute('SELECT * FROM mentor_records ORDER BY kind,id'))) == before
    assert service.runtime.store.read('conversation', identifier, Conversation) == current


def test_mobile_handoff_keeps_existing_draft_cleanup_owner(mobile):
    from riji_agent.mobile.roundtables import MobileRoundtables
    service, _, _, _ = mobile
    sentinel = object()
    service.runtime.history.drafts = sentinel
    MobileRoundtables(service)
    assert service.runtime.history.drafts is sentinel


@pytest.mark.parametrize('scenario', ['planning', 'disagreement', 'changed_conditions', 'partial_stop', 'later_feedback'])
def test_five_real_orchestration_scenarios(mobile, scenario):
    service, client, provider, _ = mobile
    identifier = create(mobile)
    generator = None
    if scenario == 'disagreement':
        from test_mentor_discussions import SyntheticGenerator
        generator = SyntheticGenerator()
        service.runtime.worker.generator = generator
    original = invite(mobile, identifier, mode='debate' if scenario == 'disagreement' else 'reference')
    if scenario == 'partial_stop':
        service.runtime.worker.run_one(identifier)
        service.runtime.worker.dispatcher.dispatch_one(identifier)
        view = client.get('/mobile/v1/roundtables/' + identifier).json()
        post(client, 'roundtables/' + identifier + '/commands', operation_id='stop', action='stop', expected_revision=view['revision'])
        assert client.get('/mobile/v1/roundtables/' + identifier).json()['status'] == 'stopped'
        post(client, 'roundtables/' + identifier + '/commands', operation_id='partial', action='summarize', expected_revision=view['revision'])
    elif scenario == 'changed_conditions':
        view = client.get('/mobile/v1/roundtables/' + identifier).json()
        post(client, 'roundtables/' + identifier + '/commands', operation_id='change', action='correct',
             expected_revision=view['revision'], text='Only ten minutes remain', supersedes=[original['messages'][0]['id']])
        updated = client.get('/mobile/v1/roundtables/' + identifier).json()
        assert any(item['superseded'] for item in updated['messages'])
        post(client, 'roundtables/' + identifier + '/commands', operation_id='resume', action='resume', expected_revision=updated['revision'])
    drain(service, identifier)
    if scenario == 'later_feedback':
        view = client.get('/mobile/v1/roundtables/' + identifier).json()
        post(client, 'roundtables/' + identifier + '/messages', operation_id='feedback', text='I tried it once; this is feedback',
             expected_revision=view['revision'], statement_kind='user_feedback')
        drain(service, identifier)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert view['status'] in {'completed', 'partial'}
    assert any(item['origin_kind'] == 'ai_discussion' and item['uncertainties'] for item in view['messages'])
    assert provider.calls
    if generator is not None:
        assert [request.stage for request in generator.calls].count('debate') == 2
        assert any(item['kind'] == 'synthesis' for item in view['messages'])


def test_source_revocation_before_model_history_result_and_receipt_replay(mobile):
    from dataclasses import replace
    from riji_agent.mobile.memory import memory_view
    service, client, provider, paired = mobile
    service.runtime.service.policy.sources.primary.service = service.memory.service
    identifier = create(mobile)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    memory = service.memory.item('synthetic-owner', 'memory-one')
    body = dict(expected_revision=view['revision'], artifact_ids=[view['messages'][0]['id']],
                memories=[{'id': memory.id, 'expected_version': memory_view(memory)['version']}],
                personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    result = post(client, 'roundtables/' + identifier + '/commands', operation_id='source-start', action='start_run',
                  expected_revision=view['revision'], personas=body['personas'], mode='reference', rounds=1, preview_hash=preview['preview_hash'])
    assert result['state'] == 'completed', result
    running = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert running['material_sources'][0]['text'] == memory.content
    assert running['progress']['actor'] == 'gentle_reviewer'
    drain(service, identifier)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    chosen = [item['id'] for item in view['messages'] if item['kind'] == 'comparison']
    saved = post(client, 'roundtables/' + identifier + '/result-preview', operation_id='result', artifact_ids=chosen)
    service.memory.service.set_memory_permission(memory.id, user_id='synthetic-owner', permission='none')
    refreshed = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert any(item['unavailable'] and not item['text'] for item in refreshed['messages'])
    assert refreshed['material_sources'][0]['unavailable']
    assert not refreshed['material_sources'][0]['text']
    assert client.post('/mobile/v1/roundtables/' + identifier + '/result-validation', json={'result_id': saved['result']['result_id']}).status_code == 409
    assert post(client, 'roundtables/' + identifier + '/result-preview', operation_id='result', artifact_ids=chosen)['result'] == saved['result']


def test_result_validation_racing_correction_is_rejected(mobile, monkeypatch):
    from riji_agent.mentors.models import Command
    service, client, _, _ = mobile
    identifier = create(mobile)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    preview = post(client, 'roundtables/' + identifier + '/result-preview', operation_id='preview-race', artifact_ids=[view['messages'][-1]['id']])
    original = service.roundtables.handoff._check
    calls = 0
    def changed(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 1:
            service.runtime.service.apply(Command(id='racing-correction', principal_id=result.principal_id, conversation_id=identifier,
                kind='correct', expected_revision=view['revision'], text='Changed premise', supersedes=(view['messages'][0]['id'],)))
        return result
    monkeypatch.setattr(service.roundtables.handoff, '_check', changed)
    response = client.post('/mobile/v1/roundtables/' + identifier + '/result-validation', json={'result_id': preview['result']['result_id']})
    assert response.status_code == 409
    assert 'Synthetic provider result' not in response.text


def test_changed_history_cursor_and_operation_fingerprint(mobile, monkeypatch):
    import riji_agent.mobile.service as module
    service, client, _, _ = mobile
    identifier = create(mobile)
    monkeypatch.setattr(module, 'MAX_CONVERSATION_MESSAGES', 1)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    assert view['next_cursor']
    post(client, 'roundtables/' + identifier + '/messages', operation_id='next-message', expected_revision=view['revision'], text='Supplement')
    assert client.get('/mobile/v1/roundtables/' + identifier, params={'cursor': view['next_cursor']}).status_code == 409
    response = client.post('/mobile/v1/roundtables', json={'operation_id': 'create-rt', 'text': 'Different content'})
    assert response.status_code == 409
    assert post(client, 'roundtables', operation_id='create-rt', text='Plan a small weekly learning experiment')['result']['conversation_id'] == identifier


def test_exact_proxy_roundtable_routes():
    from riji_agent.mobile.proxy import admitted
    for method, suffix in [('GET', 'roundtables'), ('POST', 'roundtables'), ('GET', 'roundtables/one'),
                           ('POST', 'roundtables/one/messages'), ('POST', 'roundtables/one/commands'),
                           ('POST', 'roundtables/one/materials/preview'), ('POST', 'roundtables/one/result-preview'),
                           ('POST', 'roundtables/one/result-validation')]:
        assert admitted(method, ('/mobile/v1/' + suffix).encode(), b'')
    assert admitted('GET', b'/mobile/v1/roundtables/one', b'cursor=abc')
    assert not admitted('POST', b'/mobile/v1/roundtables/one/result-validation', b'cursor=abc')
    assert not admitted('GET', b'/mobile/v1/roundtables/one/result-preview', b'')


def test_host_continuation_keeps_delivered_advice_and_correction_removes_old_inputs(mobile):
    service, client, provider, _ = mobile
    identifier = create(mobile)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    post(client, 'roundtables/' + identifier + '/messages', operation_id='reply-to-advice', expected_revision=view['revision'], text='Explain your suggestion')
    drain(service, identifier)
    request = json.loads(provider.calls[-1][0][-1]['content'])
    assert any(item['kind'] == 'ai_advice' and view['messages'][-1]['id'] in item['artifact_ids'] for item in request['working_summary']['items'])
    current = client.get('/mobile/v1/roundtables/' + identifier).json()
    post(client, 'roundtables/' + identifier + '/commands', operation_id='correct-input', action='correct', expected_revision=current['revision'],
         text='Now the plan is a ten minute experiment', supersedes=[view['messages'][0]['id']])
    drain(service, identifier)
    request = json.loads(provider.calls[-1][0][-1]['content'])
    assert view['messages'][0]['id'] not in json.dumps(request)


def test_revocation_before_execution_makes_zero_model_calls(mobile):
    from riji_agent.mobile.memory import memory_view
    service, client, provider, paired = mobile
    service.runtime.service.policy.sources.primary.service = service.memory.service
    identifier = create(mobile)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    memory = service.memory.item('synthetic-owner', 'memory-one')
    body = dict(expected_revision=view['revision'], artifact_ids=[view['messages'][0]['id']],
        memories=[{'id': memory.id, 'expected_version': memory_view(memory)['version']}],
        personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    post(client, 'roundtables/' + identifier + '/commands', operation_id='before-model', action='start_run', expected_revision=view['revision'],
         personas=body['personas'], mode='reference', rounds=1, preview_hash=preview['preview_hash'])
    before = len(provider.calls)
    service.memory.service.set_memory_permission(memory.id, user_id='synthetic-owner', permission='none')
    drain(service, identifier)
    assert len(provider.calls) == before
    assert client.get('/mobile/v1/roundtables/' + identifier).json()['status'] == 'interrupted'


def test_swift_rendered_result_fixture_is_never_personal_evidence():
    from pathlib import Path
    from riji_agent.journal.content import content_spans, personal_body
    text = (Path(__file__).parent / 'fixtures/mobile-ai-result.md').read_text()
    spans = content_spans(text)
    assert len(spans) == 1
    assert spans[0].content_type == 'ai_discussion_result'
    assert spans[0].provenance.result_id == 'result-one'
    assert not personal_body(text).strip()


def select_memory(mobile, identifier):
    from riji_agent.mobile.memory import memory_view
    service, client, _, _ = mobile
    service.runtime.service.policy.sources.primary.service = service.memory.service
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    memory = service.memory.item('synthetic-owner', 'memory-one')
    body = dict(expected_revision=view['revision'], artifact_ids=[view['messages'][0]['id']],
        memories=[{'id': memory.id, 'expected_version': memory_view(memory)['version']}],
        personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    post(client, 'roundtables/' + identifier + '/commands', operation_id='selected-memory', action='start_run',
         expected_revision=view['revision'], personas=body['personas'], mode='reference', rounds=1,
         preview_hash=preview['preview_hash'])
    return memory


def test_revocation_between_generation_and_delivery_exposes_no_new_ai(mobile):
    service, client, provider, _ = mobile
    identifier = create(mobile)
    memory = select_memory(mobile, identifier)
    before = client.get('/mobile/v1/roundtables/' + identifier).json()['messages']
    service.runtime.worker.run_one(identifier)
    assert len(provider.calls) == 2
    service.memory.service.set_memory_permission(memory.id, user_id='synthetic-owner', permission='none')
    drain(service, identifier)
    after = client.get('/mobile/v1/roundtables/' + identifier).json()['messages']
    assert {item['id'] for item in after if item['actor'] != 'user'} == {item['id'] for item in before if item['actor'] != 'user'}
    assert len(provider.calls) == 2


def test_history_revoked_after_assembly_is_rechecked_before_return(mobile, monkeypatch):
    service, client, _, _ = mobile
    identifier = create(mobile)
    memory = select_memory(mobile, identifier)
    drain(service, identifier)
    original = service.runtime.history.read
    calls = 0
    def racing_read(*args):
        nonlocal calls
        view = original(*args)
        calls += 1
        if calls == 1:
            service.memory.service.set_memory_permission(memory.id, user_id='synthetic-owner', permission='none')
        return view
    monkeypatch.setattr(service.runtime.history, 'read', racing_read)
    response = client.get('/mobile/v1/roundtables/' + identifier)
    assert response.status_code == 409
    assert 'Synthetic provider result' not in response.text


def test_reselection_cannot_smuggle_old_ai_dependency_back_into_model(mobile):
    service, client, provider, _ = mobile
    identifier = create(mobile)
    first = client.get('/mobile/v1/roundtables/' + identifier).json()
    old_user, old_host = first['messages']
    post(client, 'roundtables/' + identifier + '/messages', operation_id='new-premise', expected_revision=first['revision'], text='New independent problem')
    drain(service, identifier)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    new_user = next(item for item in view['messages'] if item['text'] == 'New independent problem')
    body = dict(expected_revision=view['revision'], artifact_ids=[new_user['id'], old_host['id']], memories=[],
                personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    rejected = client.post('/mobile/v1/roundtables/' + identifier + '/materials/preview', json=body)
    assert rejected.status_code == 409
    body['artifact_ids'] = [new_user['id']]
    preview = post(client, 'roundtables/' + identifier + '/materials/preview', **body)
    accepted = post(client, 'roundtables/' + identifier + '/commands', operation_id='independent-run', action='start_run',
        expected_revision=view['revision'], personas=body['personas'], mode='reference', rounds=1, preview_hash=preview['preview_hash'])
    assert accepted['state'] == 'completed'
    before = len(provider.calls)
    drain(service, identifier)
    for call in provider.calls[before:]:
        request = json.loads(call[0][-1]['content'])
        assert old_user['id'] not in json.dumps(request)
        assert old_host['id'] not in json.dumps(request)
        assert old_user['text'] not in json.dumps(request)


@pytest.mark.parametrize('statement_kind', [None, 'user_statement', 'user_plan', 'user_feedback'])
def test_first_message_preserves_explicit_statement_kind(mobile, statement_kind):
    service, client, provider, _ = mobile
    body = {'operation_id': 'typed-create', 'text': 'Synthetic first message'}
    if statement_kind is not None:
        body['statement_kind'] = statement_kind
    receipt = post(client, 'roundtables', **body)
    assert receipt['state'] == 'completed'
    identifier = receipt['result']['conversation_id']
    drain(service, identifier)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    expected = statement_kind or 'user_statement'
    assert view['messages'][0]['origin_kind'] == expected
    request = json.loads(provider.calls[0][0][-1]['content'])
    assert request['working_summary']['items'][0]['kind'] == expected
    selected = post(client, 'roundtables/' + identifier + '/result-preview',
                    operation_id='typed-result', artifact_ids=[view['messages'][0]['id']])
    value = post(client, 'roundtables/' + identifier + '/result-validation',
                 result_id=selected['result']['result_id'])
    label = {'user_statement': '用户原话（讨论引用）', 'user_plan': '用户陈述的计划',
             'user_feedback': '用户报告的实际反馈'}[expected]
    assert label in value['text']


def test_first_message_rejects_invalid_statement_kind_before_creation(mobile):
    service, client, provider, _ = mobile
    response = client.post('/mobile/v1/roundtables', json={
        'operation_id': 'invalid-type', 'text': 'Synthetic message', 'statement_kind': 'ai_discussion'})
    assert response.status_code == 422
    assert client.get('/mobile/v1/roundtables').json()['items'] == []
    assert not provider.calls


def test_older_page_messages_remain_selectable_for_materials_and_local_results(mobile, monkeypatch):
    import riji_agent.mobile.service as module
    service, client, _, _ = mobile
    identifier = create(mobile)
    original = client.get('/mobile/v1/roundtables/' + identifier).json()
    post(client, 'roundtables/' + identifier + '/messages', operation_id='next-page-message',
         expected_revision=original['revision'], text='Synthetic later feedback')
    drain(service, identifier)
    monkeypatch.setattr(module, 'MAX_CONVERSATION_MESSAGES', 2)
    newest = client.get('/mobile/v1/roundtables/' + identifier).json()
    older = client.get('/mobile/v1/roundtables/' + identifier,
                       params={'cursor': newest['next_cursor']}).json()
    assert [item['id'] for item in older['messages']] == [item['id'] for item in original['messages']]
    selected = post(client, 'roundtables/' + identifier + '/result-preview', operation_id='older-result',
                    artifact_ids=[older['messages'][-1]['id']])
    assert selected['state'] == 'completed'
    value = post(client, 'roundtables/' + identifier + '/result-validation', result_id=selected['result']['result_id'])
    assert value['provenance']['artifact_ids'] == [older['messages'][-1]['id']]
    preview = post(client, 'roundtables/' + identifier + '/materials/preview',
                   expected_revision=newest['revision'], artifact_ids=[older['messages'][0]['id']],
                   memories=[], personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    accepted = post(client, 'roundtables/' + identifier + '/commands', operation_id='older-invitation',
                    action='start_run', expected_revision=newest['revision'],
                    personas=preview['personas'], mode=preview['mode'], rounds=preview['rounds'],
                    preview_hash=preview['preview_hash'])
    assert accepted['state'] == 'completed'


def test_full_history_selection_still_rejects_undelivered_ai(mobile):
    service, client, _, _ = mobile
    identifier = create(mobile)
    original = client.get('/mobile/v1/roundtables/' + identifier).json()
    post(client, 'roundtables/' + identifier + '/messages', operation_id='pending-message',
         expected_revision=original['revision'], text='Synthetic pending reply')
    assert service.runtime.worker.run_one(identifier)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    pending = next(item for item in service.runtime.store.list('artifact', identifier, Artifact)
                   if item.kind != 'user' and item.run_id == current.run_id)
    body = dict(expected_revision=current.input_revision,
                artifact_ids=[original['messages'][0]['id'], pending.id], memories=[],
                personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
    assert client.post('/mobile/v1/roundtables/' + identifier + '/materials/preview', json=body).status_code == 409
    rejected = post(client, 'roundtables/' + identifier + '/result-preview',
                    operation_id='undelivered-result', artifact_ids=[pending.id])
    assert rejected['state'] == 'failed' and rejected['error'] == 'handoff_selection_invalid'


@pytest.mark.parametrize('endpoint', ['materials/preview', 'result-preview'])
def test_full_history_selection_rechecks_source_changes_before_return(mobile, monkeypatch, endpoint):
    from riji_agent.mobile.memory import memory_view
    service, client, _, _ = mobile
    identifier = create(mobile)
    memory = select_memory(mobile, identifier)
    drain(service, identifier)
    view = client.get('/mobile/v1/roundtables/' + identifier).json()
    original = service.runtime.history.read
    calls = 0

    def racing_read(*args):
        nonlocal calls
        value = original(*args)
        calls += 1
        if calls == 1:
            service.memory.service.set_memory_permission(memory.id, user_id='synthetic-owner', permission='none')
        return value

    monkeypatch.setattr(service.runtime.history, 'read', racing_read)
    if endpoint == 'materials/preview':
        body = dict(expected_revision=view['revision'], artifact_ids=[view['messages'][0]['id']],
                    memories=[{'id': memory.id, 'expected_version': memory_view(memory)['version']}],
                    personas=['gentle_reviewer', 'blunt_coach'], mode='reference', rounds=1)
        response = client.post('/mobile/v1/roundtables/' + identifier + '/' + endpoint, json=body)
        assert response.status_code == 409
    else:
        body = dict(operation_id='racing-result', artifact_ids=[view['messages'][-1]['id']])
        response = post(client, 'roundtables/' + identifier + '/' + endpoint, **body)
        assert response['state'] == 'failed'
    assert calls >= 2


def seed_history_list(mobile, count=205):
    service, _, _, device = mobile
    identifier = create(mobile)
    current = service.runtime.store.read('conversation', identifier, Conversation)
    binding = service.store.binding(device['device_id'], 'host')
    with service.runtime.store.transaction() as db:
        for number in range(count - 1):
            item = current.model_copy(update={'id': f'synthetic-history-{number:04}',
                'question': f'Synthetic history {number}', 'updated_at': 1.0})
            service.runtime.store.put(db, 'conversation', item, current.owner_id)
            service.runtime.store.bind(db, 'origin', item.id, binding)
    return current


def test_history_list_pages_are_bounded_stable_and_complete(mobile):
    _, client, _, _ = mobile
    current = seed_history_list(mobile)
    pages = [client.get('/mobile/v1/roundtables').json()]
    assert client.get('/mobile/v1/roundtables').json() == pages[0]
    while pages[-1]['next_cursor']:
        pages.append(client.get('/mobile/v1/roundtables', params={'cursor': pages[-1]['next_cursor']}).json())
    assert [len(page['items']) for page in pages] == [100, 100, 5]
    assert len({item['id'] for page in pages for item in page['items']}) == 205
    assert len({page['history_version'] for page in pages}) == 1
    assert [page['truncated'] for page in pages] == [True, True, False]
    assert all(page['current_id'] == current.id for page in pages)


@pytest.mark.parametrize('change', ['title', 'current', 'delete'])
def test_history_list_rejects_cursor_after_source_list_changes(mobile, change):
    service, client, _, device = mobile
    current = seed_history_list(mobile, count=101)
    first = client.get('/mobile/v1/roundtables').json()
    with service.runtime.store.transaction() as db:
        if change == 'current':
            service.runtime.store.bind(db, 'mobile_current_roundtable', device['device_id'], 'synthetic-history-0000')
        else:
            changes = {'question': 'Changed synthetic title'} if change == 'title' else {'status': 'deleted'}
            service.runtime.store.put(db, 'conversation', current.model_copy(update=changes), current.owner_id)
    response = client.get('/mobile/v1/roundtables', params={'cursor': first['next_cursor']})
    assert response.status_code == 409 and response.json()['detail'] == 'roundtable_list_changed'
    assert client.get('/mobile/v1/roundtables').json()['history_version'] != first['history_version']


def test_history_list_cursor_and_current_are_device_scoped(mobile):
    service, client, _, original = mobile
    current = seed_history_list(mobile, count=101)
    first = client.get('/mobile/v1/roundtables').json()
    other = service.store.provision(current.owner_id, 'Other synthetic device')
    client.headers['Authorization'] = 'Bearer ' + other['device_token']
    assert client.get('/mobile/v1/roundtables').json()['items'] == []
    assert client.get('/mobile/v1/roundtables', params={'cursor': first['next_cursor']}).status_code == 409
    created = post(client, 'roundtables', operation_id='other-create', text='Other device private host chat')
    second = client.get('/mobile/v1/roundtables').json()
    assert [item['id'] for item in second['items']] == [created['result']['conversation_id']]
    assert second['current_id'] == created['result']['conversation_id']
    client.headers['Authorization'] = 'Bearer ' + original['device_token']
    assert client.get('/mobile/v1/roundtables').json() == first


def test_history_list_rechecks_device_revocation_before_return(mobile, monkeypatch):
    service, client, _, paired = mobile
    seed_history_list(mobile, count=101)
    original = service.roundtables._list_snapshot

    def racing_snapshot(device):
        snapshot = original(device)
        service.store.revoke(paired['device_id'])
        return snapshot

    monkeypatch.setattr(service.roundtables, '_list_snapshot', racing_snapshot)
    response = client.get('/mobile/v1/roundtables')
    assert response.status_code == 401
    assert 'Synthetic history' not in response.text


def test_history_list_rechecks_source_mutation_before_return(mobile, monkeypatch):
    service, client, _, _ = mobile
    current = seed_history_list(mobile, count=101)
    original = service.roundtables._list_snapshot

    def racing_snapshot(device):
        snapshot = original(device)
        with service.runtime.store.transaction() as db:
            service.runtime.store.put(db, 'conversation', current.model_copy(
                update={'question': 'Changed during assembly'}), current.owner_id)
        return snapshot

    monkeypatch.setattr(service.roundtables, '_list_snapshot', racing_snapshot)
    response = client.get('/mobile/v1/roundtables')
    assert response.status_code == 409 and response.json()['detail'] == 'roundtable_list_changed'
    assert 'Synthetic history' not in response.text


@pytest.mark.parametrize('cursor', ['invalid', 'bnVsbA', 'e30', 'WzEsdHJ1ZV0', 'a' * 513])
def test_history_list_malformed_cursor_is_rejected(mobile, cursor):
    _, client, _, _ = mobile
    response = client.get('/mobile/v1/roundtables', params={'cursor': cursor})
    assert response.status_code in {409, 422}
