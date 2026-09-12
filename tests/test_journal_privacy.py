from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.journal.index import JournalIndex
from riji_agent.journal.parser import parse_note
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_privacy import PURPOSES
from riji_agent.memory.journal_types import JournalMemoryError
from riji_agent.memory.review import build_memory_review_router
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note
from test_mem0_long_term_memory import _settings


def authorize(engine, **changes):
    values = dict.fromkeys(PURPOSES, True)
    values.update(changes)
    engine.privacy.grant(engine.privacy.binding, values)
    engine.store.set_control('paused', '0')


def test_missing_consent_and_changed_destination_never_send(tmp_path):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.privacy.revoke()
    engine.store.set_control('paused', '0')
    assert not engine.process_next() and not model.calls
    authorize(engine)
    changed = JournalMemoryEngine(replace(engine.policy, extraction_destination='https://new.example'), engine.store, engine.backend, model)
    assert not changed.privacy.status()['valid']
    assert not changed.process_next() and not model.calls


@pytest.mark.parametrize('purpose', ['history', 'incremental'])
def test_history_snapshot_and_incremental_permissions_are_independent(tmp_path, purpose):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.scan()
    authorize(engine, history=purpose == 'history', incremental=purpose == 'incremental')
    write_note(engine.policy.root, 'daily/2026-09-01.md', '新增版本希望每周坚持跑步三次。')
    engine.scan()
    while engine.process_next():
        pass
    payloads = [json.loads(call[-1]['content']) for call in model.calls]
    texts = [row['text'] for row in payloads if 'text' in row]
    assert len(texts) == 1
    assert ('新增版本' in texts[0]) == (purpose == 'incremental')


@pytest.mark.parametrize('permission', ['local', 'none', 'typo', False])
def test_source_permission_blocks_extraction_and_stale_retrieval(tmp_path, permission):
    engine, model = runtime(tmp_path)
    path = write_note(engine.policy.root, text='这是公开学习计划，希望理解长期记忆。')
    index = JournalIndex(tmp_path / 'index.sqlite3', engine.policy.root)
    index.build_index()
    value = 'false' if permission is False else permission
    write_note(engine.policy.root, extra=f'---\nmemory: {value}\n---\n')
    assert index.search('学习', include_private=False)  # Deliberately stale index.
    assert index.cloud_hits(index.search('学习'), '学习') == []
    assert index.current_cloud_note('riji/daily/2026/08/2026-08-01') is None
    assert not engine.process_next() and not model.calls
    assert path.read_text().startswith('---')


@pytest.mark.parametrize('block', [
    '<!-- riji-memory:local -->\n保密片段不应发送云端。\n<!-- /riji-memory -->',
    '<!-- riji-memory:none -->\n保密片段不应发送云端。',
    '<!-- riji-memory:local -->\n<!-- riji-memory:cloud -->\n保密片段不应发送云端。\n<!-- /riji-memory -->\n<!-- /riji-memory -->',
])
def test_local_blocks_are_filtered_without_rewriting_source(tmp_path, block):
    engine, model = runtime(tmp_path)
    path = write_note(engine.policy.root, text='公开目标是学会 Python。\n\n' + block)
    original = path.read_bytes()
    note = parse_note(path, engine.policy.root)
    assert '保密片段' not in note.body
    engine.scan()
    while engine.process_next():
        pass
    assert '保密片段' not in json.dumps(model.calls, ensure_ascii=False)
    assert path.read_bytes() == original


def test_revocation_during_request_rejects_returned_memory(tmp_path):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    complete = model.complete
    def revoked(messages, tools):
        result = complete(messages, tools)
        engine.privacy.revoke()
        return result
    model.complete = revoked
    assert engine.process_next()
    assert len(model.calls) == 1 and not engine.backend.records
    assert not engine.process_next()


def test_recall_off_keeps_local_memory_and_prevents_derived_context(tmp_path):
    engine, model = runtime(tmp_path)
    write_note(engine.policy.root)
    authorize(engine, recall=False)
    assert engine.process_next()
    service = attached_service(tmp_path, engine)
    assert len(service.list_memories(user_id='u1')) == 1
    assert not service.retrieve('学习', user_id='u1', persona_id='coach').shared
    authorize(engine)
    assert service.retrieve('学习', user_id='u1', persona_id='coach').shared
    item = service.backend.get('m1')
    service.backend.update(item.id, metadata=dict(item.metadata, privacy='local'))
    assert not service.retrieve('学习', user_id='u1', persona_id='coach').shared
    assert service.backend.get(item.id).content


def test_source_restriction_overrides_manual_correction(tmp_path):
    engine, _ = runtime(tmp_path)
    write_note(engine.policy.root)
    engine.process_next()
    service = attached_service(tmp_path, engine)
    service.update_memory('m1', user_id='u1', content='用户手动修正后的目标。')
    write_note(engine.policy.root, extra='---\nmemory: local\n---\n')
    assert not service.retrieve('目标', user_id='u1', persona_id='coach').shared
    assert service.backend.get('m1').content == '用户手动修正后的目标。'


def test_review_persistent_banner_scope_binding_and_csrf(tmp_path):
    engine, _ = runtime(tmp_path)
    engine.privacy.revoke()
    write_note(engine.policy.root)
    engine.scan()
    service = attached_service(tmp_path, engine)
    settings = _settings(tmp_path)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    url = '/admin/memory/api/journal/authorize'
    assert client.post(url, json={}).status_code == 401
    client.post('/admin/memory/login', json={'token': settings.memory_review_token.get_secret_value()})
    for view in ['overview', 'facts', 'sources', 'compare', 'lifecycle', 'privacy']:
        page = client.get('/admin/memory', params={'view': view})
        assert 'aria-label="当前隐私权限"' in page.text
        assert '撤回日记授权' in page.text and 'position:sticky' in page.text
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)[1]
    headers = {'X-CSRF-Token': csrf}
    body = dict(user_id='u1', binding=engine.privacy.binding, permissions=dict.fromkeys(PURPOSES, True), acknowledged=True)
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, json=dict(body, user_id='u2'), headers=headers).status_code == 400
    assert client.post(url, json=dict(body, binding='outdated'), headers=headers).status_code == 409
    assert client.post(url, json=dict(body, acknowledged=False), headers=headers).status_code == 400
    assert client.post('/admin/memory/api/journal/resume', json={'user_id':'u1'}, headers=headers).status_code == 409
    assert client.post(url, json=body, headers=headers).status_code == 200
    assert engine.store.get_control('paused') == '1'
    assert client.post('/admin/memory/api/journal/resume', json={'user_id':'u1'}, headers=headers).status_code == 200
    assert engine.process_next()
    change = dict(user_id='u1', memory_id='m1', permission='local')
    assert client.post('/admin/memory/api/journal/permission', json=change, headers=headers).status_code == 200
    assert not service.retrieve('学习', user_id='u1', persona_id='coach').shared
