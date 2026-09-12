"""Execute the installed group hook with a synthetic SDK event and transport."""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from riji_agent.integrations.hermes_group_hook import (
    GROUP_BEGIN_MARKER, GROUP_END_MARKER, group_bridge_block,
)
from riji_agent.integrations.hermes_installer import (
    ANCHOR, BEGIN_MARKER, END_MARKER, install, status, uninstall,
)


def raw_event():
    return {
        "schema": "2.0",
        "header": {"app_id": "synthetic_app", "tenant_key": "synthetic_tenant",
                   "event_type": "im.message.receive_v1", "event_id": "synthetic_event"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "synthetic_owner"},
                       "tenant_key": "synthetic_tenant"},
            "message": {"message_id": "synthetic_message", "chat_id": "synthetic_group",
                        "chat_type": "group", "message_type": "text",
                        "content": json.dumps({"text": "Synthetic private message."}),
                        "mentions": []},
        },
    }


@pytest.fixture
def execute_hook(monkeypatch):
    monkeypatch.setenv("RIJI_AGENT_URL", "http://127.0.0.1:8765/hermes/messages")
    monkeypatch.setenv("HERMES_SHARED_SECRET", "example-shared-secret")
    requests, private_calls, clients = [], [], []
    state = {"response": httpx.Response(202, json={"status": "accepted"}), "error": None}

    class Client:
        def __init__(self, **kwargs):
            clients.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requests.append({"url": url, **kwargs})
            if state["error"]:
                raise state["error"]
            return state["response"]

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    namespace = {
        "os": __import__("os"), "Platform": SimpleNamespace(FEISHU="feishu"),
        "logger": logging.getLogger("synthetic.hermes"), "private_calls": private_calls,
    }
    code = ("async def dispatch(self, event, source, is_internal=False):\n"
            + group_bridge_block() + "\n        private_calls.append(True)\n        return 'private'\n")
    exec(compile(code, "synthetic-installed-hook", "exec"), namespace)

    def execute(raw=None, chat_type="group", platform="feishu", is_internal=False):
        event = SimpleNamespace(raw_message=raw)
        source = SimpleNamespace(chat_type=chat_type, platform=platform)
        return asyncio.run(namespace["dispatch"](None, event, source, is_internal))

    return SimpleNamespace(execute=execute, requests=requests, private_calls=private_calls,
                           clients=clients, state=state)


def test_group_hook_preserves_sdk_identity_and_backend_delivery(execute_hook):
    raw = raw_event()
    assert execute_hook.execute(raw) is None
    assert execute_hook.requests == [{
        "url": "http://127.0.0.1:8765/api/mentors/v1/host-events",
        "headers": {"X-Hermes-Secret": "example-shared-secret"},
        "json": {"raw_event": raw},
    }]
    assert execute_hook.clients == [{"timeout": 20.0, "trust_env": False, "follow_redirects": False}]
    assert not execute_hook.private_calls


def test_typed_sdk_event_is_serialized_without_new_identity(execute_hook):
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    raw = P2ImMessageReceiveV1(raw_event())
    assert execute_hook.execute(raw) is None
    forwarded = execute_hook.requests[0]["json"]["raw_event"]
    assert forwarded["header"]["app_id"] == "synthetic_app"
    assert forwarded["event"]["message"]["message_id"] == "synthetic_message"
    assert forwarded["event"]["sender"]["sender_type"] == "user"
    assert not execute_hook.private_calls


@pytest.mark.parametrize("chat_type", ["dm", "p2p"])
def test_private_route_is_preserved(execute_hook, chat_type):
    assert execute_hook.execute(raw_event(), chat_type=chat_type) == "private"
    assert not execute_hook.requests
    assert execute_hook.private_calls == [True]


@pytest.mark.parametrize("raw", [None, {}, {"header": {}}, {"event": {}},
                                 {"header": [], "event": {}},
                                 {"header": {}, "event": {"data": "x" * 100000}}])
def test_malformed_group_does_not_fall_through(execute_hook, raw):
    assert execute_hook.execute(raw) is None
    assert not execute_hook.requests
    assert not execute_hook.private_calls


@pytest.mark.parametrize("url", ["https://external.invalid/hermes/messages",
                                  "http://127.0.0.1:8766/hermes/messages",
                                  "http://127.0.0.1:8765/other",
                                  "http://user@127.0.0.1:8765/hermes/messages",
                                  "http://127.0.0.1:8765/hermes/messages?x=1"])
def test_group_secret_never_follows_configured_external_url(execute_hook, monkeypatch, url):
    monkeypatch.setenv("RIJI_AGENT_URL", url)
    assert execute_hook.execute(raw_event()) is None
    assert not execute_hook.requests
    assert not execute_hook.private_calls


@pytest.mark.parametrize("status_code", [200, 202, 302, 401, 403, 409, 503])
def test_http_results_never_echo_or_fall_back(execute_hook, status_code):
    execute_hook.state["response"] = httpx.Response(status_code, json={"reply": "must not echo"})
    assert execute_hook.execute(raw_event()) is None
    assert len(execute_hook.requests) == 1
    assert not execute_hook.private_calls


def test_failure_does_not_retry_or_log_text(execute_hook, caplog):
    execute_hook.state["error"] = httpx.ReadTimeout("example-shared-secret Synthetic private message.")
    assert execute_hook.execute(raw_event()) is None
    assert len(execute_hook.requests) == 1
    assert not execute_hook.private_calls
    assert "example-shared-secret" not in caplog.text
    assert "Synthetic private message" not in caplog.text


def test_duplicate_delivery_preserves_message_key(execute_hook):
    execute_hook.execute(raw_event())
    execute_hook.execute(raw_event())
    assert execute_hook.requests[0]["json"] == execute_hook.requests[1]["json"]


def test_other_profiles_and_platforms_keep_their_routes(execute_hook, monkeypatch):
    assert execute_hook.execute(raw_event(), platform="other") == "private"
    monkeypatch.delenv("RIJI_AGENT_URL")
    monkeypatch.delenv("HERMES_SHARED_SECRET")
    assert execute_hook.execute(raw_event()) == "private"
    assert not execute_hook.requests


@pytest.mark.parametrize("missing", ["RIJI_AGENT_URL", "HERMES_SHARED_SECRET"])
def test_partial_riji_configuration_does_not_fall_through(execute_hook, monkeypatch, missing):
    monkeypatch.delenv(missing)
    assert execute_hook.execute(raw_event()) is None
    assert not execute_hook.requests
    assert not execute_hook.private_calls


def test_upgrade_preserves_existing_private_extensions_and_uninstalls(tmp_path: Path):
    path = tmp_path / "gateway.py"
    private = BEGIN_MARKER + "\n        private_image_extension = True\n" + END_MARKER + "\n"
    original = "async def dispatch():\n" + private + ANCHOR + "\n        pass\n"
    path.write_text(original)
    install(path)
    first = path.read_text()
    assert private in first
    assert first.index(GROUP_BEGIN_MARKER) < first.index(BEGIN_MARKER)
    assert status(path).group_installed
    assert (tmp_path / "gateway.py.riji-agent.bak").read_text() == original
    install(path)
    assert path.read_text() == first
    uninstall(path)
    assert GROUP_BEGIN_MARKER not in path.read_text()
    assert GROUP_END_MARKER not in path.read_text()
    assert BEGIN_MARKER not in path.read_text()
    assert not status(path).group_installed
