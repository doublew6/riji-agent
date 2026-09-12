from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.memory.review import build_memory_review_router
from riji_agent.memory.privacy_ui import privacy_banner
from test_journal_lifecycle import attached_service
from test_journal_memory import runtime, write_note
from test_journal_privacy import authorize
from test_mem0_long_term_memory import _settings
from test_journal_initialization_budget import initialized
from test_journal_recovery_controls import failed_seed


def review_client(tmp_path, engine):
    service = attached_service(tmp_path, engine)
    settings = _settings(tmp_path)
    app = FastAPI()
    app.include_router(build_memory_review_router(service, settings))
    client = TestClient(app)
    response = client.post('/admin/memory/login', json={
        'token': settings.memory_review_token.get_secret_value(),
    })
    assert response.status_code == 200
    return client


def test_daily_budget_is_visible_on_every_review_page(tmp_path):
    engine, _ = runtime(tmp_path, daily_chars=100000)
    engine.store.reserve_daily(engine.policy, 123)
    client = review_client(tmp_path, engine)
    for view in ('overview', 'sources', 'privacy', 'facts'):
        response = client.get('/admin/memory', params={'view': view})
        assert response.status_code == 200
        assert '日常处理每日上限 100000 字符（UTC 日）' in response.text
        assert '当日已用 123 字符' in response.text
        assert '本次初始化不限每日总量' not in response.text


def test_initialization_mode_and_retained_privacy_limits_are_visible(tmp_path):
    engine, _ = runtime(tmp_path, daily_chars=100000, initialization_unlimited=True)
    write_note(engine.policy.root)
    engine.scan()
    authorize(engine)
    client = review_client(tmp_path, engine)
    for view in ('overview', 'sources', 'privacy'):
        response = client.get('/admin/memory', params={'view': view})
        assert response.status_code == 200
        assert '本次初始化不限每日总量' in response.text
        assert '日常处理每日上限 100000 字符（UTC 日）' in response.text
        assert '初始化累计已计量 0 字符' in response.text
        assert '撤回日记授权' in response.text
    page = client.get('/admin/memory', params={'view': 'privacy'}).text
    assert '每片段 900 字符，同一来源版本累计 4000 字符' in page
    assert '后续新增或修改的日记按日常预算处理' in page
    assert 'private: true' in page


def test_finished_initialization_banner_returns_to_daily_mode(tmp_path):
    engine, _ = runtime(tmp_path, daily_chars=100000, initialization_unlimited=True)
    write_note(engine.policy.root)
    engine.scan()
    authorize(engine, organization=False)
    assert engine.process_next()
    assert engine.initialization_status()['state'] == 'completed'
    page = review_client(tmp_path, engine).get('/admin/memory').text
    assert '本次初始化已结束，不限额处理已关闭' in page
    assert '日常处理每日上限 100000 字符（UTC 日）' in page
    assert '本次初始化不限每日总量' not in page
    assert '历史提取完成（1/1 个片段）' in page
    assert '自动处理已开启' in page
    assert '>暂停自动处理</button>' in page


def registered_recovery(tmp_path):
    engine, _ = initialized(tmp_path, organization=True, daily_chars=100000)
    ref = failed_seed(engine)
    engine.store.set_control('initialization_state', 'blocked')
    assert engine.store.recover_initialization_seeds([ref]) == 1
    return engine, ref


def test_fixed_recovery_budget_is_visible_without_reopening_blocked_batch(tmp_path):
    engine, _ = registered_recovery(tmp_path)
    engine.store.reserve_daily(engine.policy, 100000)
    client = review_client(tmp_path, engine)
    for view in ('overview', 'sources', 'privacy', 'facts'):
        page = client.get('/admin/memory', params={'view': view}).text
        assert '本次固定批次有限恢复不限每日总量（每个失败 ID / 版本仅一次）' in page
        assert '原批次状态：有失败待处理' in page
        assert '本批整理未完成（待处理 0、恢复中 1）' in page
        assert '不会自动重试' not in page.split('</aside>', 1)[0]
        assert '适用初始化账本的有限恢复：待发送 1、已发送待结果 0' in page
        assert '初始化累计已计量 25 字符' in page
        assert '当日已用 100000 字符' in page
        assert '失败重试计入日常上限' not in page
    assert not engine.initialization_status()['active']
    privacy = client.get('/admin/memory', params={'view': 'privacy'}).text
    assert '不重新开放原批次；恢复完成或失败后不能自动再试' in privacy
    assert '旧账本不清零或追溯转移' in privacy


def test_inflight_recovery_remains_visible_until_its_result_is_terminal(tmp_path):
    engine, ref = registered_recovery(tmp_path)
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    client = review_client(tmp_path, engine)
    page = client.get('/admin/memory').text
    assert '本次固定批次有限恢复不限每日总量' in page
    assert '待发送 0、已发送待结果 1' in page
    assert '初始化累计已计量 75 字符' in page
    engine.store.finish_initialization_seed(*ref, 'failed')
    page = client.get('/admin/memory').text
    assert '本次固定批次有限恢复不限每日总量' not in page
    assert '本次初始化处理已结束，不限额处理已关闭' in page
    assert '本批整理有 1 项失败（无恢复任务，不会自动重试）' in page
    assert '待发送 0、已发送待结果 0' in page
    assert '失败 1、恢复队列 0' in page


@pytest.mark.parametrize('boundary', ('disabled', 'changed_scope', 'cancelled'))
def test_ineligible_recovery_does_not_show_active_exemption(tmp_path, boundary):
    engine, _ = registered_recovery(tmp_path)
    if boundary == 'disabled':
        engine.policy = replace(engine.policy, initialization_unlimited=False)
    elif boundary == 'changed_scope':
        engine.policy = replace(engine.policy, sections=('Other',))
    else:
        engine.store.set_control('initialization_state', 'cancelled')
    page = review_client(tmp_path, engine).get('/admin/memory').text
    assert '本次固定批次有限恢复不限每日总量' not in page
    assert '本次初始化不限每日总量' not in page
    assert '日常处理每日上限 100000 字符（UTC 日）' in page
    if boundary != 'disabled':
        assert '适用初始化账本的有限恢复：待发送 0、已发送待结果 0' in page


@pytest.mark.parametrize('outcome', ('pending', 'failed', 'excluded'))
def test_partial_history_never_claims_extraction_complete(tmp_path, outcome):
    engine, _ = initialized(tmp_path, count=2)
    assert engine.process_next()
    if outcome == 'failed':
        row = engine.store.rows("SELECT id,version FROM evidence WHERE status='pending'")[0]
        engine.store.execute("UPDATE evidence SET status='failed' WHERE id=?", (row['id'],))
        engine.advance_initialization()
    elif outcome == 'excluded':
        engine.store.execute("UPDATE evidence SET active=0 WHERE status='pending'")
        engine.advance_initialization()
    page = review_client(tmp_path, engine).get('/admin/memory').text
    assert '历史提取完成' not in page
    assert ('历史提取已结束' if outcome == 'excluded' else '历史提取未完成') in page
    assert '（1/2 个片段）' in page


def test_finished_extraction_and_failed_organization_are_separate_on_every_page(tmp_path):
    engine, ref = registered_recovery(tmp_path)
    assert engine.process_next()
    engine.store.reserve_daily(engine.policy, 50, initialization_seed=ref)
    engine.store.finish_initialization_seed(*ref, 'failed')
    client = review_client(tmp_path, engine)
    for view in ('overview', 'sources', 'privacy', 'facts', 'compare', 'lifecycle'):
        page = client.get('/admin/memory', params={'view': view}).text
        assert '历史提取完成（1/1 个片段） · 本批整理有 1 项失败' in page
        assert '>暂停自动处理</button>' in page
        assert '>暂停提取</button>' not in page
    engine.store.set_control('paused', '1')
    for view in ('overview', 'sources', 'privacy'):
        page = client.get('/admin/memory', params={'view': view}).text
        assert '自动处理已暂停' in page
        assert '>继续自动处理</button>' in page
        assert '历史提取完成（1/1 个片段）' in page


def test_budget_wait_is_distinct_from_fixed_history_coverage(tmp_path):
    engine, _ = initialized(tmp_path)
    assert engine.process_next()
    service = attached_service(tmp_path, engine)
    run_id = service.operations.organization.request(engine.policy.user_id)
    assert service.operations.organization.claim()['id'] == run_id
    service.operations.organization.defer(run_id, 3600, 'journal_daily_budget')
    page = SimpleNamespace(service=service, user_id=engine.policy.user_id)
    banner = privacy_banner(page)
    assert '历史提取完成（1/1 个片段）' in banner
    assert '后台整理等待日常额度（每日 00:00 UTC 重置）' in banner
    engine.store.set_control('paused', '1')
    assert '后台整理等待日常额度' not in privacy_banner(page)
