"""Synthetic transport crash boundaries; no Feishu network or model calls."""

import json
import secrets
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from riji_agent.mentors.models import Envelope, MentorError, TransportResult
from riji_agent.mentors.receiver import enqueue_event
from riji_agent.mentors.receiver_spool import ReceiverSpool
from riji_agent.mentors.receiver_worker import ReceiverWorker


@pytest.fixture
def transport(tmp_path):
    now = [1000.0]
    folder = tmp_path / "transport"
    folder.mkdir(mode=0o700)
    spool = ReceiverSpool(folder / "spool.sqlite3", "synthetic-app", lambda: now[0], 2)
    message = Envelope(delivery_id="event", message_id="message", external_user_id="open-test",
        external_chat_id="chat-test", chat_type="p2p", text="Synthetic question")
    posts, sends = [], []
    def forward(request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "Accepted"})
    def send(delivery, text):
        sends.append((delivery, text))
        return TransportResult(status="sent", message_id="receipt-test")
    http = httpx.Client(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(forward))
    channel = SimpleNamespace(send=send)
    worker = ReceiverWorker(spool, http, channel)
    yield SimpleNamespace(spool=spool, message=message, now=now, worker=worker, posts=posts, sends=sends, channel=channel)
    http.close()


def reopen(s):
    return ReceiverSpool(s.spool.path, "synthetic-app", lambda: s.now[0], 2)


def test_queued_message_survives_restart_and_duplicate_does_not_send_twice(transport):
    s = transport
    identifier = s.spool.enqueue(s.message)
    s.worker.spool = reopen(s)
    assert s.worker.step()
    assert s.posts == [s.message.model_dump(mode="json")]
    delivery, text = s.sends[0]
    assert delivery.application_id == "synthetic-app" and delivery.chat_id == "chat-test"
    assert delivery.uuid == identifier[:32] and text == "Accepted"
    s.worker.spool.enqueue(s.message.model_copy(update={"delivery_id": "redelivery"}))
    assert not s.worker.step() and len(s.posts) == len(s.sends) == 1
    with s.worker.spool.transaction() as db:
        row = db.execute("SELECT * FROM receipts").fetchone()
        assert row["state"] == "done" and row["message_id"] == "receipt-test" and row["payload"] == ""


def test_queue_capacity_and_conflicting_payloads_fail_before_business_calls(transport):
    s = transport
    s.spool.enqueue(s.message)
    s.spool.enqueue(s.message.model_copy(update={"message_id": "second"}))
    with pytest.raises(MentorError, match="receiver_spool_full"):
        s.spool.enqueue(s.message.model_copy(update={"message_id": "third"}))
    with pytest.raises(MentorError, match="receiver_event_conflict"):
        s.spool.enqueue(s.message.model_copy(update={"text": "changed"}))
    assert not s.posts and not s.sends


@pytest.mark.parametrize("state", ["processing", "sending"])
def test_interrupted_attempt_never_replays_after_restart(transport, state):
    s = transport
    identifier = s.spool.enqueue(s.message)
    assert s.spool.take()[0] == identifier
    if state == "sending":
        s.spool.mark(identifier, state)
    s.worker.spool = reopen(s)
    assert s.worker.spool.status() == {"unknown": 1}
    assert not s.worker.step() and not s.posts and not s.sends


def test_live_status_does_not_apply_restart_recovery(transport):
    from riji_agent.mentors.receiver_spool import read_spool_status
    s = transport
    s.spool.enqueue(s.message)
    s.spool.take()
    assert read_spool_status(s.spool.path) == {"processing": 1}
    assert s.spool.status() == {"processing": 1}


@pytest.mark.parametrize("failure", ["timeout", "missing_receipt", "unknown_receipt", "http_timeout", "server_error"])
def test_uncertain_business_or_send_result_is_recorded_without_retry(transport, failure):
    s = transport
    def fail(*args, **kwargs):
        raise TimeoutError("Synthetic sensitive response must not be logged")
    if failure == "timeout":
        s.channel.send = fail
    elif failure == "http_timeout":
        s.worker.transport = SimpleNamespace(post=fail)
    elif failure == "server_error":
        s.worker.transport = SimpleNamespace(post=lambda *a, **k: httpx.Response(503, json={"detail": "unavailable"}))
    else:
        s.channel.send = lambda *a: TransportResult(status="sent" if failure == "missing_receipt" else "unknown")
    s.spool.enqueue(s.message)
    assert s.worker.step() and s.spool.status() == {"unknown": 1}
    s.worker.spool = reopen(s)
    assert not s.worker.step()


def test_pairing_secret_never_reaches_sqlite_and_requires_regeneration_after_restart(transport):
    s = transport
    token = secrets.token_urlsafe(32)
    message = s.message.model_copy(update={"text": "/绑定 " + token})
    s.spool.enqueue(message)
    with s.spool.transaction() as db:
        assert token not in "\n".join(db.iterdump())
    assert token.encode() not in s.spool.path.read_bytes()
    assert reopen(s).status() == {"expired": 1}


def test_pairing_can_complete_without_persistent_plaintext(transport):
    s = transport
    message = s.message.model_copy(update={"text": "/绑定 " + secrets.token_urlsafe(32)})
    s.spool.enqueue(message)
    assert s.worker.step() and s.posts == [message.model_dump(mode="json")]
    assert s.spool.status() == {"done": 1}


def test_queued_expiry_and_terminal_retention_are_bounded(transport):
    s = transport
    s.spool.enqueue(s.message)
    s.now[0] += 86401
    assert not s.worker.step() and s.spool.status() == {"expired": 1}
    s.now[0] += 7 * 86400
    assert s.spool.status() == {}


def test_private_spool_rejects_symlinks_and_public_parent(tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(MentorError, match="permissions_invalid"):
        ReceiverSpool(public / "db", "app")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / "db").symlink_to(tmp_path / "target")
    with pytest.raises(MentorError, match="path_invalid"):
        ReceiverSpool(private / "db", "app")


def sdk_event(s, *, chat_type="p2p", message_type="text", sender="user"):
    pytest.importorskip("lark_oapi")
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    return P2ImMessageReceiveV1({"schema": "2.0", "header": {"event_id": "event", "app_id": "synthetic-app"},
        "event": {"sender": {"sender_type": sender, "tenant_key": "tenant-test", "sender_id": {"open_id": "open-test"}},
            "message": {"message_id": "message", "chat_id": "chat-test", "chat_type": chat_type,
                "message_type": message_type, "content": json.dumps({"text": s.message.text})}}})


def test_sdk_callback_only_commits_and_wakes_then_unsupported_gets_fixed_reply(transport):
    s = transport
    app = SimpleNamespace(external_id="synthetic-app", tenant="tenant-test", persona_id="gentle_reviewer")
    enqueue_event(sdk_event(s, message_type="image"), app, s.worker)
    assert not s.posts and not s.sends
    assert s.worker.step() and not s.posts
    assert "文字" in s.sends[0][1]


@pytest.mark.parametrize("change", [{"chat_type": "group"}, {"sender": "app"}])
def test_mentor_group_copies_and_bot_events_do_not_queue(transport, change):
    s = transport
    app = SimpleNamespace(external_id="synthetic-app", tenant="tenant-test", persona_id="gentle_reviewer")
    enqueue_event(sdk_event(s, **change), app, s.worker)
    assert s.spool.status() == {} and not s.posts


def test_sdk_callback_storage_failure_propagates_only_safe_error(transport):
    s = transport
    app = SimpleNamespace(external_id="synthetic-app", tenant="tenant-test", persona_id="gentle_reviewer")
    def fail(message):
        raise OSError("Synthetic private filesystem information")
    s.spool.enqueue = fail
    with pytest.raises(MentorError, match="^receiver_enqueue_failed$"):
        enqueue_event(sdk_event(s), app, s.worker)


def test_hermes_owned_host_cannot_start_dedicated_receiver(tmp_path):
    from riji_agent.mentors.configuration import ApplicationConfig
    from riji_agent.mentors.receiver import run
    application = ApplicationConfig(external_id="existing-host", tenant="tenant-test",
                                    persona_id="host", receiver="hermes")
    config = SimpleNamespace(applications=(application,), feishu_receiver_ownership="dedicated_apps")
    with pytest.raises(MentorError, match="dedicated_feishu_application_required"):
        run(config, application.external_id, "http://127.0.0.1:8765", tmp_path / "locks", tmp_path / "vault")


def test_hermes_receiver_must_match_the_configured_original_application(tmp_path):
    from riji_agent.mentors.runtime import build_runtime
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"applications": [{"external_id": "other-app", "tenant": "synthetic",
        "persona_id": "host", "receiver": "hermes"}], "users": [{"account": {"platform": "local",
        "tenant": "synthetic", "subject": "user"}, "legacy_owner_key": "user", "review_token_env": "SYNTHETIC_REVIEW"}],
        "feishu_receiver_ownership": "dedicated_apps"}))
    path.chmod(0o600)
    settings = SimpleNamespace(mentors_enabled=True, mentors_config_path=path, port=8765,
        journal_root=tmp_path / "vault", data_dir=tmp_path / "data", feishu_app_id="original-app")
    with pytest.raises(MentorError, match="hermes_host_application_mismatch"):
        build_runtime(settings, model=None, memory_service=None, drafts=None)


def test_sdk_log_filter_removes_external_response_and_traceback():
    import logging
    from riji_agent.mentors.feishu import SafeSDKLogs
    record = logging.LogRecord("Lark", logging.ERROR, "synthetic.py", 1,
        "External response %s", ("Synthetic confidential content",), (ValueError, ValueError("private"), None))
    assert SafeSDKLogs().filter(record)
    assert record.getMessage() == "feishu_sdk_transport_event" and record.exc_info is None


def test_hermes_host_runtime_has_no_dedicated_transport_token(tmp_path, monkeypatch):
    from riji_agent.mentors.runtime import build_runtime
    monkeypatch.setenv("SYNTHETIC_HOST_SECRET", secrets.token_urlsafe(32))
    monkeypatch.setenv("SYNTHETIC_REVIEW", secrets.token_urlsafe(32))
    monkeypatch.setattr("riji_agent.mentors.runtime.build_client", lambda *args: object())
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"applications": [{"external_id": "original-app", "tenant": "synthetic",
        "persona_id": "host", "receiver": "hermes", "secret_env": "SYNTHETIC_HOST_SECRET"}],
        "users": [{"account": {"platform": "local", "tenant": "synthetic", "subject": "user"},
        "legacy_owner_key": "user", "review_token_env": "SYNTHETIC_REVIEW"}],
        "feishu_receiver_ownership": "dedicated_apps"}))
    path.chmod(0o600)
    settings = SimpleNamespace(mentors_enabled=True, mentors_config_path=path, port=8765,
        journal_root=tmp_path / "vault", data_dir=tmp_path / "data", feishu_app_id="original-app")
    settings.feishu_app_secret = SecretStr("synthetic-original-host-secret")
    runtime = build_runtime(settings, model=None, memory_service=None, drafts=None)
    try:
        assert runtime.legacy_host.external_id == "original-app" and not runtime.transport_tokens
    finally:
        runtime.graph.close()
