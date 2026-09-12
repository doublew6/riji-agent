"""Synthetic raw SDK ingress; no fixture certifies production group permissions."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from riji_agent.mentors.api import build_router
from riji_agent.mentors.host_bridge import HostGroupBridge, HostNotice, HostReceipt, register_host_owners
from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.ingress import DiscussionIngress
from riji_agent.mentors.models import (
    Account, Application, Artifact, AudienceGrant, Conversation, MentorError, Principal, RoomSnapshot, TransportResult,
)
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import MentorStore, key
from riji_agent.personas.registry import PersonaRegistry

pytest.importorskip("lark_oapi")


class Sources:
    def background(self, *args):
        raise AssertionError("A group bridge diagnostic must not retrieve sources")

    def validate(self, *args):
        raise AssertionError("A group bridge diagnostic must not validate private sources")


def raw_event(text="Synthetic ordinary input", **changes):
    header = {"event_id": "delivery-1", "app_id": "original-host",
              "tenant_key": "test-tenant", "event_type": "im.message.receive_v1"}
    sender = {"sender_type": "user", "tenant_key": "test-tenant",
              "sender_id": {"open_id": "owner-open"}}
    message = {"message_id": "message-1", "chat_id": "verified-room", "chat_type": "group",
               "message_type": "text", "create_time": "1000000", "content": json.dumps({"text": text}), "mentions": []}
    header.update(changes.pop("header", {}))
    sender.update(changes.pop("sender", {}))
    message.update(changes.pop("message", {}))
    return {"schema": "2.0", "header": header, "event": {"sender": sender, "message": message}, **changes}


@pytest.fixture
def host(tmp_path):
    store = MentorStore(tmp_path / "mentors.sqlite3")
    identity = IdentityService(store, PersonaRegistry())
    owner = identity.register_principal(Account(platform="local", tenant="web", subject="web-owner"), "owner-open")
    app = identity.register_application(Application(platform="feishu", tenant="test-tenant",
        external_id="original-host", persona_id="host", role="host"))
    snapshot = RoomSnapshot(room_id="verified-room", human_subjects=(owner.account.subject,),
        application_ids=(app.id,), private=True, complete=True, history_restricted=True,
        management_restricted=True, continuity_verified=True, configuration_version="synthetic-only")
    evidence = SimpleNamespace(snapshot=snapshot, human_open_ids=("owner-open",),
        known_application_ids=(app.id,), human_pages_complete=True, known_bot_count_matches=True, settings_stable=True)
    observed = []
    def inspect_evidence(room):
        observed.append(room)
        if room != snapshot.room_id:
            raise MentorError("unknown_synthetic_room")
        return evidence
    sent = []
    def send(delivery, text):
        sent.append((delivery, text))
        return TransportResult(status="sent", message_id="synthetic-sent")
    channel = SimpleNamespace(inspect_room=lambda room: evidence.snapshot, send=send,
        adapters={"feishu": SimpleNamespace(inspect_room_evidence=inspect_evidence)})
    service = DiscussionService(store, identity, DiscussionPolicy(store, Sources(), channel))
    wakes = []
    worker = SimpleNamespace(wake=lambda: wakes.append(True))
    history = DiscussionHistory(service)
    ingress = DiscussionIngress(service, worker, history)
    runtime = SimpleNamespace(store=store, identity=identity, service=service, history=history, ingress=ingress,
        worker=worker, legacy_host=app, user_tokens={"owner-review": owner.id}, transport_tokens={})
    register_host_owners(runtime, frozenset({"owner-open"}))
    clock = [1000.0]
    bridge = HostGroupBridge(runtime, "example-hermes-secret", frozenset({"owner-open"}), lambda: clock[0])
    runtime.host_bridge = bridge
    conversation = Conversation(owner_id=owner.id, kind="roundtable", question="Synthetic problem",
        personas=("gentle_reviewer", "blunt_coach"), mode="reference", status="completed",
        room_status="ended", room_id=snapshot.room_id, grant_id="synthetic-grant", created_at=1000, updated_at=1000)
    grant = AudienceGrant(id=conversation.grant_id, conversation_id=conversation.id, owner_id=owner.id,
                         input_revision=1, snapshot=snapshot, source_versions={})
    with store.transaction() as db:
        store.put(db, "conversation", conversation, owner.id)
        service._user_artifact(db, conversation, conversation.question)
        conversation = service.summaries.refresh(db, conversation)
        store.put(db, "conversation", conversation, owner.id)
        service.budgets.create(db, conversation)
        service.summaries.record_run(db, conversation)
        store.put(db, "grant", grant, owner.id)
        store.bind(db, "room", key(app.platform, app.tenant, snapshot.room_id), conversation.id)
    api = FastAPI()
    api.include_router(build_router(runtime))
    with TestClient(api) as client:
        yield SimpleNamespace(**runtime.__dict__, bridge=bridge, owner=owner, app=app, client=client,
            conversation=conversation, evidence=evidence, observed=observed, clock=clock, wakes=wakes,
            sent=sent,
            headers={"X-Hermes-Secret": "example-hermes-secret"}, owner_headers={"Authorization": "Bearer owner-review"})


def post(host, raw):
    return host.client.post("/api/mentors/v1/host-events", json={"raw_event": raw}, headers=host.headers)


def challenge(host, room="verified-room"):
    return host.client.post("/api/mentors/v1/host-diagnostics", json={"expected_chat_id": room}, headers=host.owner_headers)


def test_secret_and_review_authentication_are_separate(host):
    path = "/api/mentors/v1/host-events"
    for headers in ({}, host.owner_headers, {"X-Hermes-Secret": "wrong"}):
        assert host.client.post(path, json={"raw_event": raw_event()}, headers=headers).status_code == 401
    assert host.client.post("/api/mentors/v1/host-diagnostics", json={"expected_chat_id": "verified-room"},
                            headers=host.headers).status_code == 401
    assert host.client.get("/api/mentors/v1/host-status").status_code == 401
    assert not host.wakes


@pytest.mark.parametrize("changes", [
    {"header": {"app_id": "other-app"}}, {"header": {"event_id": ""}},
    {"header": {"tenant_key": "other-tenant"}}, {"header": {"event_type": "im.chat.disbanded_v1"}},
    {"schema": "1.0"},
    {"sender": {"sender_type": "app"}}, {"sender": {"tenant_key": "other-tenant"}},
    {"message": {"chat_type": "p2p"}}, {"message": {"message_id": ""}},
    {"message": {"message_type": "image"}},
    {"message": {"create_time": ""}}, {"message": {"create_time": "1000000000"}},
    {"message": {"mentions": [{"key": "@x", "name": "x" * 101}]}},
])
def test_raw_sdk_identity_and_group_shape_are_required(host, changes):
    response = post(host, raw_event(**changes))
    assert response.status_code == 409 and response.json()["status"] == "rejected"
    assert response.json()["code"] == "host_group_event_invalid"
    assert response.json()["hermes_reply"] is False and not host.wakes


def test_spoofed_subject_does_not_replace_original_app_scoped_owner(host):
    response = post(host, raw_event(sender={"sender_id": {"open_id": "stranger-open", "user_id": "web-owner"}}))
    assert response.status_code == 409 and response.json()["code"] == "host_sender_not_allowed"
    assert len(host.store.list("principal", "", Principal)) == 1
    assert not host.wakes


def test_unknown_or_incomplete_group_never_reaches_a_model(host):
    response = post(host, raw_event(message={"chat_id": "unknown-room"}))
    assert response.json()["status"] == "rejected" and response.json()["code"] == "unmanaged_group"
    host.evidence.snapshot = host.evidence.snapshot.model_copy(update={"complete": False})
    response = post(host, raw_event(message={"message_id": "second-message"}))
    assert response.json()["status"] == "rejected"
    assert response.json()["code"] == "audience_verification_failed"
    assert not host.wakes


def test_ordinary_input_uses_existing_ingress_once_and_never_returns_reply_text(host):
    first = post(host, raw_event())
    assert first.status_code == 200 and first.json()["status"] == "accepted"
    assert first.json()["delivery"] == "backend_only" and first.json()["hermes_reply"] is False
    assert "text" not in first.json() and "reply" not in first.json()
    duplicate = post(host, raw_event(header={"event_id": "redelivery"}))
    assert duplicate.json()["duplicate"] and duplicate.json()["receipt_id"] == first.json()["receipt_id"]
    current = host.service.get(host.conversation.id, host.owner.id)
    assert current.followup_actor == "host" and current.status == "queued" and current.run_number == 1
    items = host.store.list("artifact", current.id, Artifact)
    assert sum(item.text == "Synthetic ordinary input" for item in items) == 1 and len(host.wakes) == 1
    conflict = post(host, raw_event("请大家辩论一下"))
    assert conflict.status_code == 409 and conflict.json()["code"] == "host_event_conflict"


def test_explicit_multi_mentor_command_starts_one_bounded_run(host):
    response = post(host, raw_event("请大家重新辩论"))
    assert response.json()["status"] == "accepted"
    current = host.service.get(host.conversation.id, host.owner.id)
    assert current.run_number == 2 and current.mode == "debate" and current.run_kind == "roundtable"
    assert len(host.service.budgets.status(current)["budgets"]) == 2


def test_diagnostic_requires_observed_room_and_never_reads_sources_or_calls_ingress(host):
    assert challenge(host, "unknown-room").status_code == 409
    created = challenge(host).json()
    before = host.conversation
    host.evidence.snapshot = host.evidence.snapshot.model_copy(update={"complete": False, "history_restricted": False,
                                                                      "continuity_verified": False})
    response = post(host, raw_event(created["command"]))
    assert response.json()["code"] == "host_diagnostic_received" and response.json()["status"] == "accepted"
    status = host.client.get("/api/mentors/v1/host-diagnostics/" + created["id"], headers=host.owner_headers).json()
    assert status["status"] == "received" and status["discussions_enabled"] is False
    assert host.service.get(before.id, host.owner.id) == before and not host.wakes
    assert created["command"].split()[1].encode() not in host.store.path.read_bytes()
    assert b"Synthetic ordinary input" not in host.store.path.read_bytes()


@pytest.mark.parametrize("change", ["room", "sender", "expiry", "replaced", "reuse"])
def test_diagnostic_nonce_is_bound_expiring_and_single_use(host, change):
    created = challenge(host).json()
    changes = {}
    if change == "room":
        changes["message"] = {"chat_id": "other-room"}
    elif change == "sender":
        changes["sender"] = {"sender_id": {"open_id": "stranger-open"}}
    elif change == "expiry":
        host.clock[0] += 601
    elif change == "replaced":
        challenge(host)
    else:
        assert post(host, raw_event(created["command"])).json()["status"] == "accepted"
        changes["message"] = {"message_id": "reused-token-message"}
    response = post(host, raw_event(created["command"], **changes))
    assert response.json()["status"] == "rejected" and not host.wakes


def test_diagnostic_status_is_owner_only_and_missing_raw_fields_are_safe(host):
    created = challenge(host).json()
    host.user_tokens["other-review"] = "other-owner"
    response = host.client.get("/api/mentors/v1/host-diagnostics/" + created["id"],
                               headers={"Authorization": "Bearer other-review"})
    assert response.status_code == 409 and response.json()["detail"] == "host_diagnostic_unavailable"
    assert post(host, {"event": {}}).json()["code"] == "host_group_event_invalid"
    response = host.client.post("/api/mentors/v1/host-events", json={"raw_event": "PRIVATE_SENTINEL"}, headers=host.headers)
    assert response.status_code == 422 and "PRIVATE_SENTINEL" not in response.text


@pytest.mark.parametrize("form", ["quoted", "bare", "embedded"])
def test_diagnostic_nonce_never_becomes_ordinary_model_background(host, form):
    created = challenge(host).json()
    token = created["command"].split()[1]
    text = {"quoted": "他说：" + created["command"], "bare": token,
            "embedded": "请帮我理解这个字符串 " + token + " 的用途"}[form]
    result = post(host, raw_event(text))
    assert result.json()["status"] == "rejected" and result.json()["code"] == "host_diagnostic_invalid"
    assert not host.wakes and token.encode() not in host.store.path.read_bytes()


@pytest.mark.parametrize("field,value", [("human_pages_complete", False), ("known_bot_count_matches", False),
                                         ("settings_stable", False), ("human_open_ids", ("owner-open", "other"))])
def test_diagnostic_creation_requires_complete_current_membership(host, field, value):
    setattr(host.evidence, field, value)
    result = challenge(host)
    assert result.status_code == 409 and result.json()["detail"] == "host_diagnostic_room_unverified"
    assert not host.wakes


def test_interrupted_business_receipt_is_pending_after_recreation_without_replay(host):
    original = host.ingress.receive
    def interrupted(*args):
        original(*args)
        raise TimeoutError("Private synthetic exception must not escape")
    host.ingress.receive = interrupted
    first = post(host, raw_event())
    assert first.json()["status"] == "pending" and "Private" not in first.text
    host.host_bridge = HostGroupBridge(host, "example-hermes-secret", frozenset({"owner-open"}), lambda: host.clock[0])
    second = host.host_bridge.receive(raw_event(header={"event_id": "new-delivery"}))
    assert second["status"] == "pending" and second["duplicate"]
    assert len(host.wakes) == 1


def test_existing_legacy_owner_mapping_cannot_be_silently_reassigned(host):
    with host.store.transaction() as db:
        host.store.bind(db, "external_user", key(host.app.platform, host.app.tenant, host.app.id, "owner-open"), "wrong-owner")
    with pytest.raises(MentorError, match="host_owner_mapping_conflict"):
        register_host_owners(host, frozenset({"owner-open"}))
    response = post(host, raw_event())
    assert response.json()["code"] == "host_owner_unmapped" and not host.wakes


def test_old_real_message_cannot_be_replayed_after_receipt_retention(host):
    host.clock[0] += 8 * 86400
    response = post(host, raw_event())
    assert response.json()["status"] == "rejected" and response.json()["code"] == "host_group_event_invalid"
    assert not host.wakes


def test_control_reply_is_sent_once_by_backend_outbox(host):
    first = post(host, raw_event("未知导师，如何开始？", message={"mentions": [{"key": "@unknown", "name": "未知导师"}]}))
    assert first.json()["status"] == "accepted"
    assert host.service.get(host.conversation.id, host.owner.id) == host.conversation
    assert host.bridge.dispatch_notice()
    assert not host.bridge.dispatch_notice()
    assert len(host.sent) == 1 and "明确指定" in host.sent[0][1]
    assert host.sent[0][0].application_id == host.app.id
    notice = host.store.read("host_notice", first.json()["receipt_id"], HostNotice)
    assert notice.status == "sent" and notice.text == ""


@pytest.mark.parametrize("failure", ["unknown", "exception", "missing_receipt"])
def test_uncertain_control_delivery_never_retries(host, failure):
    first = post(host, raw_event("谢谢"))
    attempts = []
    def send(*args):
        attempts.append(True)
        if failure == "exception":
            raise TimeoutError("Private transport information")
        return TransportResult(status="unknown" if failure == "unknown" else "sent", message_id="")
    host.service.policy.channel.send = send
    assert host.bridge.dispatch_notice() and not host.bridge.dispatch_notice()
    assert len(attempts) == 1
    assert host.store.read("host_notice", first.json()["receipt_id"], HostNotice).status == "unknown"


@pytest.mark.parametrize("change", ["membership", "revision", "deleted"])
def test_control_outbox_rechecks_audience_and_current_problem_before_sending(host, change):
    first = post(host, raw_event("谢谢"))
    if change == "membership":
        host.evidence.snapshot = host.evidence.snapshot.model_copy(update={"human_subjects": ("stranger",)})
    else:
        with host.store.transaction() as db:
            current = host.store.get(db, "conversation", host.conversation.id, Conversation)
            updates = {"input_revision": current.input_revision + 1} if change == "revision" else {"status": "deleted"}
            host.store.put(db, "conversation", current.model_copy(update=updates), current.owner_id)
    host.bridge.dispatch_notice()
    assert not host.sent
    notice = host.store.read("host_notice", first.json()["receipt_id"], HostNotice)
    assert notice.status == "cancelled" and notice.text == ""


def test_sending_control_notice_becomes_unknown_after_restart(host):
    first = post(host, raw_event("谢谢"))
    assert host.bridge._claim_notice("") is not None
    restarted = HostGroupBridge(host, "example-hermes-secret", frozenset({"owner-open"}), lambda: host.clock[0])
    assert not restarted.dispatch_notice() and not host.sent
    notice = host.store.read("host_notice", first.json()["receipt_id"], HostNotice)
    assert notice.status == "unknown" and notice.text == ""


def test_failed_stop_acknowledgement_never_changes_stopped_business_state(host):
    host.evidence.snapshot = host.evidence.snapshot.model_copy(update={"complete": False})
    response = post(host, raw_event("停止讨论"))
    assert response.json()["status"] == "accepted"
    assert host.service.get(host.conversation.id, host.owner.id).status == "stopped"
    host.bridge.dispatch_notice()
    assert not host.sent and host.service.get(host.conversation.id, host.owner.id).status == "stopped"


def test_control_outbox_rechecks_current_original_host_allowlist(host):
    response = post(host, raw_event("谢谢"))
    host.bridge.allowed = frozenset()
    host.bridge.dispatch_notice()
    notice = host.store.read("host_notice", response.json()["receipt_id"], HostNotice)
    assert not host.sent and notice.status == "cancelled"


def test_control_outbox_hard_limit_rejects_before_business_but_keeps_diagnostics(host):
    diagnostic = challenge(host).json()
    with host.store.transaction() as db:
        for index in range(200):
            notice = HostNotice(id="pending-" + str(index), owner_id=host.owner.id,
                conversation_id=host.conversation.id, chat_id=host.conversation.room_id,
                input_revision=1, cancel_epoch=0, text="Synthetic pending control", created_at=host.clock[0])
            host.store.put(db, "host_notice", notice, host.owner.id)
    response = post(host, raw_event())
    assert response.status_code == 409 and response.json()["code"] == "host_control_outbox_full"
    assert not host.wakes and host.service.get(host.conversation.id, host.owner.id) == host.conversation
    assert post(host, raw_event(diagnostic["command"], message={"message_id": "diagnostic-message"})).json()["status"] == "accepted"
    assert len(host.store.list("host_notice", host.owner.id, HostNotice)) == 200


def test_terminal_control_orphans_are_pruned_without_the_original_receipt(host):
    orphan = HostNotice(id="orphan", owner_id=host.owner.id, conversation_id=host.conversation.id,
        chat_id=host.conversation.room_id, input_revision=1, cancel_epoch=0, text="",
        status="sent", created_at=host.clock[0] - 8 * 86400)
    with host.store.transaction() as db:
        host.store.put(db, "host_notice", orphan, host.owner.id)
    assert post(host, raw_event("谢谢")).json()["status"] == "accepted"
    assert host.store.read("host_notice", "orphan", HostNotice) is None


@pytest.mark.parametrize("kind", ["notices", "receipts"])
@pytest.mark.parametrize("command", ["natural", "slash"])
def test_full_bridge_queues_do_not_block_authenticated_managed_stop(host, kind, command):
    with host.store.transaction() as db:
        for index in range(200):
            identifier = "pending-" + str(index)
            if kind == "notices":
                item = HostNotice(id=identifier, owner_id=host.owner.id, conversation_id=host.conversation.id,
                    chat_id=host.conversation.room_id, input_revision=1, cancel_epoch=0,
                    text="Synthetic pending control", created_at=host.clock[0])
                host.store.put(db, "host_notice", item, host.owner.id)
            else:
                item = HostReceipt(id=identifier, owner_id=host.owner.id, fingerprint="synthetic", received_at=host.clock[0])
                host.store.put(db, "host_receipt", item, host.owner.id)
    text = "停止讨论" if command == "natural" else "/停止 " + host.conversation.id
    response = post(host, raw_event(text))
    assert response.json()["status"] == "accepted"
    assert host.service.get(host.conversation.id, host.owner.id).status == "stopped"
    duplicate = post(host, raw_event(text, header={"event_id": "redelivery"}))
    assert duplicate.json()["duplicate"]
    assert len(host.store.list("host_receipt", host.owner.id, HostReceipt)) <= 200
    assert len(host.store.list("host_notice", host.owner.id, HostNotice)) <= 200


@pytest.mark.parametrize("change", ["stranger", "unmanaged"])
def test_urgent_queue_bypass_never_bypasses_owner_or_managed_room(host, change):
    changes = {"sender": {"sender_id": {"open_id": "stranger-open"}}} if change == "stranger" else {
        "message": {"chat_id": "unmanaged-room"}}
    result = post(host, raw_event("停止讨论", **changes))
    assert result.json()["status"] == "rejected"
    assert host.service.get(host.conversation.id, host.owner.id) == host.conversation


def test_urgent_stop_does_not_wait_on_slow_group_bridge_lock(host):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    locked, release = threading.Event(), threading.Event()
    def hold():
        with host.bridge._lock:
            locked.set()
            release.wait(3)
    thread = threading.Thread(target=hold)
    thread.start()
    assert locked.wait(1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(host.bridge.receive, raw_event("停止讨论")).result(timeout=1)
        assert result["status"] == "accepted"
    finally:
        release.set()
        thread.join()


def test_runtime_registers_original_host_without_a_second_receiver(tmp_path, monkeypatch):
    from riji_agent.mentors.runtime import build_runtime
    pytest.importorskip("langgraph.checkpoint.sqlite")
    monkeypatch.setenv("TEST_ORIGINAL_HOST_SECRET", "synthetic-original-secret-" * 3)
    monkeypatch.setenv("TEST_HOST_REVIEW", "synthetic-owner-review-" * 3)
    monkeypatch.setattr("riji_agent.mentors.runtime.build_client", lambda *args: object())
    config = tmp_path / "mentor-config.json"
    config.write_text(json.dumps({"feishu_receiver_ownership": "dedicated_apps", "applications": [{
        "external_id": "original-host", "platform": "feishu", "tenant": "test-tenant", "persona_id": "host",
        "receiver": "hermes", "secret_env": "TEST_ORIGINAL_HOST_SECRET"}], "users": [{
        "account": {"platform": "local", "tenant": "web", "subject": "web-owner"},
        "legacy_owner_key": "owner-open", "review_token_env": "TEST_HOST_REVIEW"}]}))
    config.chmod(0o600)
    settings = SimpleNamespace(mentors_enabled=True, mentors_config_path=config, journal_root=tmp_path / "vault",
        data_dir=tmp_path / "data", port=8765, feishu_app_id="original-host",
        feishu_app_secret=SecretStr("synthetic-original-settings-secret"),
        allowed_feishu_user_ids=frozenset({"owner-open"}), hermes_shared_secret=SecretStr("hermes-secret"))
    runtime = build_runtime(settings, model=None, memory_service=None, drafts=None)
    try:
        assert runtime.host_bridge is not None and not runtime.transport_tokens
        assert len(runtime.store.list("principal", "", Principal)) == 1
        assert runtime.host_bridge._principal("owner-open").legacy_owner_key == "owner-open"
    finally:
        runtime.graph.close()


def test_hermes_client_reuses_settings_env_file_secret_without_copying_it(tmp_path, monkeypatch):
    import os
    from riji_agent.config import Settings
    from riji_agent.mentors.runtime import build_runtime
    pytest.importorskip("langgraph.checkpoint.sqlite")
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.setenv("TEST_HOST_ENV_REVIEW", "synthetic-review-value-" * 3)
    (tmp_path / "vault").mkdir()
    config = tmp_path / "mentor-config.json"
    config.write_text(json.dumps({"feishu_receiver_ownership": "dedicated_apps", "applications": [{
        "external_id": "original-host", "platform": "feishu", "tenant": "test-tenant", "persona_id": "host",
        "receiver": "hermes", "secret_env": "FEISHU_APP_SECRET"}], "users": [{
        "account": {"platform": "local", "tenant": "web", "subject": "web-owner"},
        "legacy_owner_key": "owner-open", "review_token_env": "TEST_HOST_ENV_REVIEW"}]}))
    config.chmod(0o600)
    environment = tmp_path / "runtime.env"
    environment.write_text("\n".join(["RIJI_JOURNAL_ROOT=" + str(tmp_path / "vault"),
        "RIJI_DATA_DIR=" + str(tmp_path / "data"), "RIJI_ALLOWED_FEISHU_USER_IDS=owner-open",
        "HERMES_SHARED_SECRET=synthetic-hermes-secret", "DEEPSEEK_API_KEY=synthetic-model-key",
        "FEISHU_APP_ID=original-host", "FEISHU_APP_SECRET=synthetic-env-file-host-secret"]))
    calls = []
    monkeypatch.setattr("riji_agent.mentors.runtime.build_client", lambda *args: calls.append(args) or object())
    settings = Settings(_env_file=environment, RIJI_MENTORS_ENABLED=True, RIJI_MENTORS_CONFIG_PATH=config)
    runtime = build_runtime(settings, model=None, memory_service=None, drafts=None)
    try:
        assert calls == [("original-host", "synthetic-env-file-host-secret")]
        assert "FEISHU_APP_SECRET" not in os.environ and runtime.host_bridge is not None
        assert not runtime.transport_tokens
    finally:
        runtime.graph.close()
