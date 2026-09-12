"""Deterministic adapter tests; no account credentials or cloud calls."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from riji_agent.models import codex
from riji_agent.models.codex import CodexProvider, parse_turn
from riji_agent.models.codex_runtime import child_environment, command
from riji_agent.models.codex_schedule import CodexScheduler
from riji_agent.models.types import LLMError

TOOL = {"type": "function", "function": {"name": "search_journal", "parameters": {"type": "object"}}}


@pytest.fixture(autouse=True)
def scheduler(monkeypatch):
    monkeypatch.setattr(codex, "SCHEDULER", CodexScheduler())


def fake_cli(tmp_path, events=None, login="Logged in using ChatGPT", wait=False, proxy_url=None):
    script = tmp_path / "fake-codex"
    events = events or [
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"content": "A synthetic answer", "tool_calls": []})}},
        {"type": "turn.completed"},
    ]
    script.write_text(f'''#!{sys.executable}
import json, sys, time
if sys.argv[1:] == ["--version"]:
 print("codex-cli 0.153.4")
 raise SystemExit(0)
if sys.argv[1:3] == ["login", "status"]:
 print({login!r})
 raise SystemExit(0)
payload = sys.stdin.read()
if {wait!r}: time.sleep(5)
for event in {events!r}: print(json.dumps(event), flush=True)
''')
    script.chmod(0o700)
    home = tmp_path / "isolated"
    home.mkdir(mode=0o700)
    return CodexProvider(str(script), "synthetic", home=home, timeout_seconds=10, proxy_url=proxy_url)


def test_official_cli_transport_calls_guard_then_parses_answer(tmp_path):
    provider = fake_cli(tmp_path)
    guards = []
    result = provider.complete_with_guard([{"role": "user", "content": "Synthetic"}], [], lambda: guards.append(True))
    assert result.content == "A synthetic answer"
    assert result.tool_calls == ()
    assert guards == [True]
    assert not list(provider.home.glob("sessions/**/*"))


def test_guard_failure_prevents_model_process(tmp_path, monkeypatch):
    provider = fake_cli(tmp_path)
    monkeypatch.setattr(provider, "_execute", lambda *args: pytest.fail("must not start model"))
    def revoked():
        raise LLMError("source_permission_revoked")
    with pytest.raises(LLMError, match="source_permission_revoked"):
        provider.complete_with_guard([], [], revoked)


def test_api_auth_rejected_before_guard_or_model(tmp_path):
    provider = fake_cli(tmp_path, login="Logged in using an API key")
    with pytest.raises(LLMError, match="codex_login_required"):
        provider.complete_with_guard([], [], lambda: pytest.fail("must not charge budget"))


def test_tools_are_plans_only_and_names_must_be_allowed():
    result = parse_turn(json.dumps({"content": "", "tool_calls": [{"name": "search_journal", "arguments": '{"query":"walk"}'}]}), [TOOL])
    assert result.tool_calls[0].name == "search_journal"
    assert json.loads(result.tool_calls[0].arguments) == {"query": "walk"}
    with pytest.raises(LLMError, match="codex_invalid_response"):
        parse_turn(json.dumps({"content": "", "tool_calls": [{"name": "commit_draft", "arguments": "{}"}]}), [TOOL])


@pytest.mark.parametrize("value", [
    {"content": None, "tool_calls": []},
    {"content": "x", "tool_calls": [{"name": "search_journal", "arguments": "[]"}]},
    {"content": "x", "tool_calls": [{"name": "search_journal", "arguments": "{"}]},
    {"content": "x", "tool_calls": [], "extra": "bad"},
    {"content": "x" * 30001, "tool_calls": []},
])
def test_malformed_outputs_fail_closed(value):
    with pytest.raises(LLMError, match="codex_invalid_response"):
        parse_turn(json.dumps(value), [TOOL])


def test_empty_tool_catalog_cannot_execute_any_tool():
    with pytest.raises(LLMError, match="codex_invalid_response"):
        parse_turn('{"content":"","tool_calls":[{"name":"search_journal","arguments":"{}"}]}', [])


@pytest.mark.parametrize("item_type", [
    "command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call", "unknown_tool",
])
def test_unexpected_native_tool_activity_aborts(tmp_path, item_type):
    provider = fake_cli(tmp_path, events=[{"type": "item.started", "item": {"type": item_type, "command": "SENSITIVE"}}])
    with pytest.raises(LLMError, match="^codex_unexpected_tool_activity$"):
        provider.complete([], [])


@pytest.mark.parametrize("event_type", ["item.started", "item.updated", "item.completed"])
@pytest.mark.parametrize("message,code", [
    ("Unsupported synthetic configuration PRIVATE original", "codex_request_failed"),
    ("usage_limit_reached PRIVATE original", "codex_quota_exhausted"),
    ("authentication failed PRIVATE original", "codex_login_required"),
])
def test_error_items_abort_with_safe_classification(tmp_path, capsys, event_type, message, code):
    provider = fake_cli(tmp_path, events=[
        {"type": event_type, "item": {"id": "synthetic-error", "type": "error", "message": message}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"content":"ignored","tool_calls":[]}'}},
        {"type": "turn.completed"},
    ])
    with pytest.raises(LLMError) as raised:
        provider.complete([], [])
    assert str(raised.value) == code
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    if code in {"codex_quota_exhausted", "codex_login_required"}:
        with pytest.raises(LLMError, match=f"^{code}$"):
            provider.complete_with_guard([], [], lambda: pytest.fail("must not send during cooldown"))


def test_exact_disabled_code_mode_notice_allows_answer(tmp_path):
    provider = fake_cli(tmp_path, events=[
        {"type": "item.completed", "item": {"type": "error", "message": codex._DISABLED_CODE_MODE_NOTICE}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"content":"Synthetic answer","tool_calls":[]}'}},
        {"type": "turn.completed"},
    ])
    assert provider.complete([], []).content == "Synthetic answer"


@pytest.mark.parametrize("message", [
    codex._DISABLED_CODE_MODE_NOTICE + " PRIVATE appended error",
    codex._DISABLED_CODE_MODE_NOTICE.replace("disabled", "enabled"),
])
def test_similar_code_mode_notice_still_aborts(tmp_path, message):
    provider = fake_cli(tmp_path, events=[
        {"type": "item.completed", "item": {"type": "error", "message": message}},
    ])
    with pytest.raises(LLMError, match="^codex_request_failed$"):
        provider.complete([], [])


def test_disabled_code_mode_notice_does_not_permit_native_tools(tmp_path):
    provider = fake_cli(tmp_path, events=[
        {"type": "item.completed", "item": {"type": "error", "message": codex._DISABLED_CODE_MODE_NOTICE}},
        {"type": "item.started", "item": {"type": "command_execution", "command": "SENSITIVE"}},
    ])
    with pytest.raises(LLMError, match="^codex_unexpected_tool_activity$"):
        provider.complete([], [])


@pytest.mark.parametrize("item", [
    None, [], {"type": "agent_message", "text": []},
    {"type": "error"}, {"type": "error", "message": None}, {"type": "error", "message": []},
])
def test_malformed_protocol_is_a_safe_error(tmp_path, item):
    provider = fake_cli(tmp_path, events=[{"type": "item.completed", "item": item}])
    with pytest.raises(LLMError, match="^codex_invalid_protocol$"):
        provider.complete([], [])


def test_errors_are_safe_and_quota_stops_subsequent_requests(tmp_path):
    provider = fake_cli(tmp_path, events=[{"type": "turn.failed", "error": {"message": "usage_limit_reached PRIVATE original"}}])
    with pytest.raises(LLMError, match="^codex_quota_exhausted$"):
        provider.complete([], [])
    with pytest.raises(LLMError, match="^codex_quota_exhausted$"):
        provider.complete_with_guard([], [], lambda: pytest.fail("must not charge budget"))


def test_timeout_terminates_owned_child(tmp_path):
    provider = fake_cli(tmp_path, wait=True)
    provider.timeout_seconds = .15
    started = time.monotonic()
    with pytest.raises(LLMError, match="codex_timeout"):
        provider.complete([], [])
    assert time.monotonic() - started < 3


def test_request_deadline_spans_all_rounds(tmp_path):
    provider = fake_cli(tmp_path)
    provider.timeout_seconds = .04
    with provider.request_scope():
        time.sleep(.06)
        with pytest.raises(LLMError, match="codex_queue_timeout"):
            provider.complete([], [])


def test_env_uses_dedicated_login_and_strips_unrelated_credentials(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "DEEPSEEK_API_KEY", "CODEX_ACCESS_TOKEN", "HERMES_SHARED_SECRET"):
        monkeypatch.setenv(name, "SENSITIVE")
    env = child_environment(tmp_path)
    assert env["CODEX_HOME"] == str(tmp_path)
    assert "SENSITIVE" not in env.values()


def test_none_proxy_preserves_inherited_routing_without_mutating_parent(tmp_path, monkeypatch):
    inherited = {"HTTP_PROXY": "http://127.0.0.1:1001", "https_proxy": "http://127.0.0.1:1002",
                 "ALL_PROXY": "socks5://127.0.0.1:1003", "NO_PROXY": "localhost,example.test"}
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    env = child_environment(tmp_path)
    assert all(env[key] == value for key, value in inherited.items())
    assert dict(os.environ) == before


def test_explicit_proxy_overrides_all_child_routes_and_remote_bypasses(tmp_path, monkeypatch):
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    for key in keys:
        monkeypatch.setenv(key, "http://127.0.0.1:1001")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "chatgpt.com")
    monkeypatch.setenv("OPENAI_API_KEY", "SENSITIVE")
    before = dict(os.environ)
    env = child_environment(tmp_path, proxy_url="http://127.0.0.1:15236")
    assert all(env[key] == "http://127.0.0.1:15236" for key in keys)
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,127.0.0.1,::1"
    assert "OPENAI_API_KEY" not in env
    assert dict(os.environ) == before


def test_proxy_reaches_auth_and_model_only_in_child_environment(tmp_path, monkeypatch, capsys, caplog):
    proxy = "http://synthetic-user:SYNTHETIC_PROXY_SECRET@127.0.0.1:15236"
    provider = fake_cli(tmp_path, proxy_url=proxy)
    before = dict(os.environ)
    original = codex.subprocess.Popen
    order = []

    def observe(argv, *args, **kwargs):
        order.append(argv[1])
        assert all(kwargs["env"][key] == proxy for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
        assert proxy not in str(argv)
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(codex.subprocess, "Popen", observe)
    result = provider.complete_with_guard([], [], lambda: order.append("guard"))
    assert result.content == "A synthetic answer"
    assert order == ["--version", "login", "guard", "exec"]
    assert dict(os.environ) == before
    captured = capsys.readouterr()
    assert "SYNTHETIC_PROXY_SECRET" not in captured.out + captured.err + caplog.text
    assert "SYNTHETIC_PROXY_SECRET" not in repr(provider)


def test_proxy_credentials_in_child_diagnostics_are_not_exposed(tmp_path, capsys, caplog):
    proxy = "http://synthetic-user:SYNTHETIC_PROXY_SECRET@127.0.0.1:15236"
    provider = fake_cli(tmp_path, proxy_url=proxy, events=[
        {"type": "turn.failed", "error": {"message": "Synthetic proxy failed: " + proxy}},
    ])
    with pytest.raises(LLMError) as raised:
        provider.complete([], [])
    assert str(raised.value) == "codex_request_failed"
    captured = capsys.readouterr()
    assert "SYNTHETIC_PROXY_SECRET" not in captured.out + captured.err + caplog.text


def test_queued_request_rechecks_guard_before_proxied_model_send(tmp_path, monkeypatch):
    provider = fake_cli(tmp_path, proxy_url="http://127.0.0.1:15236")
    permitted = [True]
    outcome, model_calls, auth_proxies = [], [], []
    monkeypatch.setattr(codex, "check_login", lambda *args, proxy_url=None: auth_proxies.append(proxy_url))
    monkeypatch.setattr(provider, "_execute", lambda *args: model_calls.append(True))

    def guard():
        if not permitted[0]:
            raise LLMError("source_permission_revoked")

    def work():
        try:
            provider.complete_with_guard([], [], guard)
        except LLMError as error:
            outcome.append(str(error))

    with codex.SCHEDULER.acquire("memory", time.monotonic() + 3):
        worker = threading.Thread(target=work)
        worker.start()
        for _ in range(100):
            if codex.SCHEDULER._chat_waiters:
                break
            time.sleep(.001)
        assert codex.SCHEDULER._chat_waiters == 1
        permitted[0] = False
    worker.join(2)
    assert not worker.is_alive()
    assert outcome == ["source_permission_revoked"]
    assert auth_proxies == ["http://127.0.0.1:15236"]
    assert not model_calls


def test_command_is_ephemeral_and_disables_capabilities(tmp_path):
    args = command("codex", "synthetic", tmp_path, tmp_path, tmp_path / "schema.json")
    assert "--ephemeral" in args and "--ignore-user-config" in args
    assert "features.shell_tool=false" in args and "features.apps=false" in args
    assert "features.plugins=false" in args and "web_search=\"disabled\"" in args
    assert "skills.include_instructions=false" in args
    assert "features.skip_host_skill_discovery=true" in args
    assert "features.code_mode.enabled=false" in args
    assert "features.code_mode=false" not in args
    assert "features.code_mode_host=false" in args
    assert "suppress_unstable_features_warning=true" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert args[-1] == "-"


def test_waiting_chat_precedes_waiting_memory():
    scheduler = CodexScheduler()
    order = []
    deadline = time.monotonic() + 3
    def work(purpose):
        with scheduler.acquire(purpose, deadline):
            order.append(purpose)
    with scheduler.acquire("memory", deadline):
        memory = threading.Thread(target=work, args=("memory",))
        chat = threading.Thread(target=work, args=("chat",))
        memory.start(); chat.start()
        for _ in range(100):
            if scheduler._chat_waiters:
                break
            time.sleep(.001)
        assert scheduler._chat_waiters == 1
    memory.join(2); chat.join(2)
    assert order == ["chat", "memory"]
