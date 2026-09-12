"""Exercise installed lifecycle callbacks using the actual SDK dispatcher."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from riji_agent.integrations.hermes_installer import (
    ANCHOR, BEGIN_MARKER, END_MARKER, HermesBridgeInstallError, install, status, uninstall,
)
from riji_agent.integrations.hermes_lifecycle_hook import LIFECYCLE_EVENTS, METHOD_BEGIN, REGISTER_BEGIN
from riji_agent.integrations.hermes_lifecycle_installer import install_text, prepare, remove_lifecycle

ADAPTER = '''class Adapter:
    def _build_event_handler(self) -> Any:
        return (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_im_chat_member_bot_deleted_v1(self._on_bot_removed_from_chat)
            .build()
        )

    def _on_bot_removed_from_chat(self, data):
        self.original_calls.append(data)
        return "original-result"

    def _on_message(self, data):
        self.message_calls.append(data)
'''


def raw_event(kind=LIFECYCLE_EVENTS[0]):
    return {"schema": "2.0", "header": {"event_id": "synthetic-event", "app_id": "original-host",
        "tenant_key": "synthetic-tenant", "create_time": "1000000", "event_type": kind},
        "event": {"chat_id": "synthetic-room", "external": False, "operator_tenant_key": "synthetic-tenant"}}


@pytest.fixture
def hook(monkeypatch):
    from lark_oapi import EventDispatcherHandler
    monkeypatch.setenv("RIJI_AGENT_URL", "http://127.0.0.1:8765/hermes/messages")
    monkeypatch.setenv("HERMES_SHARED_SECRET", "example-shared-secret")
    monkeypatch.setenv("FEISHU_APP_ID", "original-host")
    requests, options = [], []
    state = {"error": None, "status": 200}

    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            requests.append((url, kwargs))
            if state["error"]:
                raise state["error"]
            return httpx.Response(state["status"])

    monkeypatch.setattr(httpx, "Client", Client)
    scope = {"Any": object, "EventDispatcherHandler": EventDispatcherHandler,
             "os": __import__("os"), "logger": logging.getLogger("synthetic.lifecycle")}
    exec(compile(install_text(ADAPTER), "synthetic-adapter", "exec"), scope)
    adapter = scope["Adapter"]()
    adapter._app_id = "original-host"
    adapter.original_calls, adapter.message_calls = [], []
    handler = adapter._build_event_handler()

    def dispatch(raw):
        with pytest.warns(DeprecationWarning):
            return handler.do_without_validation(json.dumps(raw).encode())

    return SimpleNamespace(adapter=adapter, requests=requests, options=options, state=state, dispatch=dispatch)


@pytest.mark.parametrize("kind", LIFECYCLE_EVENTS)
def test_sdk_lifecycle_preserves_raw_scope_and_only_invalidates(hook, kind):
    hook.dispatch(raw_event(kind))
    assert len(hook.requests) == 1
    url, request = hook.requests[0]
    assert url == "http://127.0.0.1:8765/api/mentors/v1/host-lifecycle"
    assert request["headers"] == {"X-Hermes-Secret": "example-shared-secret"}
    forwarded = request["json"]["raw_event"]
    assert forwarded["schema"] == "2.0" and forwarded["header"] == raw_event(kind)["header"]
    assert forwarded["event"]["chat_id"] == "synthetic-room"
    assert hook.options == [{"timeout": 1.0, "trust_env": False, "follow_redirects": False}]
    assert len(hook.adapter.original_calls) == (kind == "im.chat.member.bot.deleted_v1")
    assert not hook.adapter.message_calls


def test_ordinary_messages_keep_their_original_callback(hook):
    event = raw_event("im.message.receive_v1")
    event["event"] = {"sender": {"sender_type": "user"}, "message": {"message_id": "message"}}
    hook.dispatch(event)
    assert len(hook.adapter.message_calls) == 1 and not hook.requests


@pytest.mark.parametrize("status_code", [302, 401, 409, 500, 503])
def test_http_failure_keeps_original_callback_without_retry(hook, status_code):
    hook.state["status"] = status_code
    hook.dispatch(raw_event("im.chat.member.bot.deleted_v1"))
    assert len(hook.requests) == len(hook.adapter.original_calls) == 1


def test_transport_failure_logs_no_raw_or_secret(hook, caplog):
    hook.state["error"] = httpx.ReadTimeout("example-shared-secret synthetic-room")
    hook.dispatch(raw_event("im.chat.member.bot.deleted_v1"))
    assert len(hook.adapter.original_calls) == len(hook.requests) == 1
    assert "example-shared-secret" not in caplog.text and "synthetic-room" not in caplog.text
    assert "invalidation unavailable" in caplog.text


@pytest.mark.parametrize("url", ["https://example.invalid/hermes/messages", "http://127.0.0.1:8766/hermes/messages",
    "http://localhost:8765/hermes/messages", "http://user@127.0.0.1:8765/hermes/messages",
    "http://127.0.0.1:8765/hermes/messages?secret=1", "http://127.0.0.1:8765/other"])
def test_secret_stays_on_exact_loopback_endpoint(hook, monkeypatch, url):
    monkeypatch.setenv("RIJI_AGENT_URL", url)
    hook.dispatch(raw_event("im.chat.member.bot.deleted_v1"))
    assert not hook.requests and len(hook.adapter.original_calls) == 1


@pytest.mark.parametrize("missing", ["RIJI_AGENT_URL", "HERMES_SHARED_SECRET", "FEISHU_APP_ID"])
def test_other_or_partial_profiles_keep_existing_behavior(hook, monkeypatch, missing):
    monkeypatch.delenv(missing)
    hook.dispatch(raw_event("im.chat.member.bot.deleted_v1"))
    assert not hook.requests and len(hook.adapter.original_calls) == 1


def test_other_app_profile_never_forwards(hook):
    hook.adapter._app_id = "other-app"
    hook.dispatch(raw_event("im.chat.member.bot.deleted_v1"))
    assert not hook.requests and len(hook.adapter.original_calls) == 1


@pytest.mark.parametrize("raw", [{}, {"schema": "1.0"}, {"schema": "2.0", "header": {}, "event": {}},
    {**raw_event(), "event": {"chat_id": "r", "data": "x" * 100000}}])
def test_malformed_payloads_are_not_forwarded(hook, raw):
    assert hook.adapter._riji_on_lifecycle_event(raw, hook.adapter._on_bot_removed_from_chat) == "original-result"
    assert not hook.requests and len(hook.adapter.original_calls) == 1


def test_install_is_reversible_and_preserves_original_source():
    patched = install_text(ADAPTER)
    assert install_text(patched) == patched
    assert remove_lifecycle(patched) == ADAPTER
    assert patched.count(METHOD_BEGIN) == patched.count(REGISTER_BEGIN) == 1


@pytest.mark.parametrize("source", [ADAPTER.replace("_on_bot_removed_from_chat)", "_other_callback)"),
    ADAPTER.replace("    def _build_event_handler", "    def other_builder"),
    install_text(ADAPTER).replace(METHOD_BEGIN, "    # missing marker")])
def test_unknown_adapter_shapes_fail_closed(source):
    with pytest.raises(HermesBridgeInstallError):
        install_text(source)


def test_combined_install_preflights_both_files_and_preserves_private_extensions(tmp_path: Path):
    gateway = tmp_path / "gateway/run.py"
    gateway.parent.mkdir()
    adapter = tmp_path / "plugins/platforms/feishu/adapter.py"
    adapter.parent.mkdir(parents=True)
    original = "async def dispatch():\n" + BEGIN_MARKER + "\n        image_and_reply_extension = True\n" + END_MARKER + "\n" + ANCHOR + "\n        pass\n"
    gateway.write_text(original)
    adapter.write_text(ADAPTER.replace("_on_bot_removed_from_chat)", "_other_callback)"))
    with pytest.raises(HermesBridgeInstallError):
        install(gateway)
    assert gateway.read_text() == original
    adapter.write_text(ADAPTER)
    result = install(gateway)
    assert result.group_installed and result.lifecycle_installed
    assert "image_and_reply_extension = True" in gateway.read_text()
    assert adapter.with_name("adapter.py.riji-agent.bak").read_text() == ADAPTER
    first = (gateway.read_bytes(), adapter.read_bytes())
    install(gateway)
    assert first == (gateway.read_bytes(), adapter.read_bytes())
    assert not adapter.with_name("adapter.py.riji-agent.bak.1").exists()
    uninstall(gateway)
    assert adapter.read_text() == ADAPTER and not status(gateway).lifecycle_installed


def test_preparation_does_not_follow_adapter_symlink(tmp_path):
    gateway = tmp_path / "gateway/run.py"
    adapter = tmp_path / "plugins/platforms/feishu/adapter.py"
    adapter.parent.mkdir(parents=True)
    target = tmp_path / "target.py"
    target.write_text(ADAPTER)
    adapter.symlink_to(target)
    with pytest.raises(HermesBridgeInstallError):
        prepare(gateway)
