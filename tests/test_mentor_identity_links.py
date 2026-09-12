"""Synthetic two-channel linking, transactional conflicts and credential boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.mentors.api import build_router
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.identity_links import IdentityLinks
from riji_agent.mentors.ingress import DiscussionIngress
from riji_agent.mentors.models import Account, Application, ChatBinding, Envelope, MentorError
from riji_agent.mentors.store import MentorStore, key
from riji_agent.personas.registry import PersonaRegistry


@pytest.fixture
def linking(tmp_path):
    store = MentorStore(tmp_path / "mentor.sqlite3")
    identity = IdentityService(store, PersonaRegistry())
    owner = identity.register_principal(Account(platform="local", tenant="local-test", subject="user"), "existing-owner")
    other = identity.register_principal(Account(platform="local", tenant="local-test", subject="other"), "other-owner")
    apps = [identity.register_application(Application(platform="feishu", tenant=tenant, external_id=external,
        persona_id=persona)) for tenant, external, persona in [
            ("tenant-test", "app-gentle", "gentle_reviewer"),
            ("tenant-test", "app-blunt", "blunt_coach"),
            ("other-tenant", "app-other", "gentle_reviewer")]]
    now = [1000.0]
    links = IdentityLinks(identity, {app.id for app in apps}, lambda: now[0])
    message = Envelope(delivery_id="event", message_id="message", external_user_id="open-synthetic",
        subject="tenant-user", external_chat_id="private-synthetic", chat_type="p2p", text="Synthetic question")
    def forbidden():
        raise AssertionError("Pairing must not wake a model worker")
    ingress = DiscussionIngress(SimpleNamespace(store=store, identity=identity), SimpleNamespace(wake=forbidden), None)
    ingress.identity_links = links
    runtime = SimpleNamespace(store=store, identity=identity, identity_links=links, ingress=ingress,
        user_tokens={"synthetic-owner-review": owner.id, "synthetic-other-review": other.id},
        transport_tokens={"synthetic-app-transport": apps[0].id, "synthetic-other-transport": apps[1].id})
    api = FastAPI()
    api.include_router(build_router(runtime))
    client = TestClient(api)
    client.headers["Authorization"] = "Bearer synthetic-owner-review"
    return SimpleNamespace(store=store, identity=identity, owner=owner, other=other, apps=apps,
        links=links, now=now, message=message, ingress=ingress, client=client, runtime=runtime)


def prepare(linking, *, owner=None, app=None, message=None):
    app = app or linking.apps[0]
    request = linking.links.create((owner or linking.owner).id, app.id)
    message = (message or linking.message).model_copy(update={"text": request["command"]})
    receipt = linking.ingress.receive(app.id, message)
    proof = receipt["text"].splitlines()[0].removeprefix("核对码：")
    return request, message, proof


def test_link_requires_both_channels_and_preserves_owner_after_restart(linking):
    s = linking
    request, message, proof = prepare(s)
    with pytest.raises(MentorError, match="identity_verification_required"):
        s.identity.resolve(s.apps[0].id, message)
    assert s.links.confirm(request["id"], s.owner.id, proof) == {"status": "connected"}
    restarted = IdentityService(MentorStore(s.store.path), PersonaRegistry())
    principal, app, binding = restarted.resolve(s.apps[0].id, message)
    assert principal.id == s.owner.id and principal.legacy_owner_key == "existing-owner"
    assert principal.account.platform == "local"
    assert binding.principal_id == s.owner.id and app.persona_id == "gentle_reviewer"
    assert s.links.confirm(request["id"], s.owner.id, proof) == {"status": "connected"}


def test_missing_subject_requires_explicit_binding_for_each_application(linking):
    s = linking
    message = s.message.model_copy(update={"subject": ""})
    request, message, proof = prepare(s, message=message)
    s.links.confirm(request["id"], s.owner.id, proof)
    assert s.identity.resolve(s.apps[0].id, message)[0].id == s.owner.id
    with pytest.raises(MentorError, match="identity_verification_required"):
        s.identity.resolve(s.apps[1].id, message)
    request2, message2, proof2 = prepare(s, app=s.apps[1], message=message.model_copy(update={
        "external_user_id": "open-second", "external_chat_id": "private-second"}))
    s.links.confirm(request2["id"], s.owner.id, proof2)
    assert s.identity.resolve(s.apps[1].id, message2)[0].legacy_owner_key == "existing-owner"


def test_stable_subject_does_not_automatically_enroll_other_apps_or_tenants(linking):
    s = linking
    request, message, proof = prepare(s)
    s.links.confirm(request["id"], s.owner.id, proof)
    for app in s.apps[1:]:
        with pytest.raises(MentorError, match="identity_verification_required"):
            s.identity.resolve(app.id, message)


@pytest.mark.parametrize("change", [{"sender_kind": "bot"}, {"chat_type": "group"}])
def test_nonprivate_and_bot_claims_rejected_before_any_binding(linking, change):
    s = linking
    request = s.links.create(s.owner.id, s.apps[0].id)
    message = s.message.model_copy(update={"text": request["command"], **change})
    with pytest.raises(MentorError, match="identity_link_private_user_required"):
        s.ingress.receive(s.apps[0].id, message)
    assert not s.store.list("chat", s.owner.id, ChatBinding)


def test_wrong_app_owner_and_transport_token_cannot_confirm(linking):
    s = linking
    request = s.links.create(s.owner.id, s.apps[0].id)
    message = s.message.model_copy(update={"text": request["command"]})
    with pytest.raises(MentorError, match="identity_link_invalid"):
        s.links.claim(s.apps[1].id, message)
    with pytest.raises(MentorError, match="identity_link_unavailable"):
        s.links.confirm(request["id"], s.other.id, "12345678")
    with pytest.raises(MentorError, match="identity_link_not_claimed"):
        s.links.confirm(request["id"], s.owner.id, "12345678")
    response = s.client.post("/api/mentors/v1/identity-links/" + request["id"] + "/confirm",
        json={"code": "12345678"}, headers={"Authorization": "Bearer synthetic-app-transport"})
    assert response.status_code == 401


def test_claim_replay_is_stable_but_another_claimant_is_rejected(linking):
    s = linking
    request, message, proof = prepare(s)
    assert proof in s.links.claim(s.apps[0].id, message.model_copy(update={"delivery_id": "replayed"}))["text"]
    for changes in [{"external_user_id": "someone-else"}, {"external_chat_id": "another-chat"}, {"subject": "other-user"}]:
        with pytest.raises(MentorError, match="identity_link_invalid"):
            s.links.claim(s.apps[0].id, message.model_copy(update=changes))
    s.links.confirm(request["id"], s.owner.id, proof)
    with pytest.raises(MentorError, match="identity_link_invalid"):
        s.links.claim(s.apps[0].id, message)


@pytest.mark.parametrize("invalidation", ["expired", "cancelled", "replaced", "attempts"])
def test_invalidated_links_cannot_be_revived(linking, invalidation):
    s = linking
    request, message, proof = prepare(s)
    if invalidation == "expired":
        s.now[0] += 600
    elif invalidation == "cancelled":
        s.links.cancel(request["id"], s.owner.id)
    elif invalidation == "replaced":
        s.links.create(s.owner.id, s.apps[0].id)
    else:
        for _ in range(5):
            with pytest.raises(MentorError, match="identity_link_code_invalid"):
                s.links.confirm(request["id"], s.owner.id, "incorrect")
    with pytest.raises(MentorError, match="identity_link_invalid"):
        s.links.confirm(request["id"], s.owner.id, proof)
    with pytest.raises(MentorError, match="identity_link_invalid"):
        s.links.claim(s.apps[0].id, message)


def test_conflicting_subject_is_not_reassigned_and_transaction_rolls_back(linking):
    s = linking
    first, message, proof = prepare(s)
    s.links.confirm(first["id"], s.owner.id, proof)
    second, _, code = prepare(s, owner=s.other, app=s.apps[1], message=message.model_copy(update={
        "external_user_id": "other-open", "external_chat_id": "other-private"}))
    with pytest.raises(MentorError, match="identity_conflict"):
        s.links.confirm(second["id"], s.other.id, code)
    assert not s.store.list("chat", s.other.id, ChatBinding)
    with s.store.transaction() as db:
        assert s.store.lookup(db, "external_user", key("feishu", "tenant-test", s.apps[1].id, "other-open")) is None
        assert s.store.lookup(db, "principal", key("feishu", "tenant-test", "tenant-user")) == s.owner.id


def test_external_conflict_rolls_back_new_subject_mapping(linking):
    s = linking
    first, message, proof = prepare(s, message=s.message.model_copy(update={"subject": ""}))
    s.links.confirm(first["id"], s.owner.id, proof)
    second, _, code = prepare(s, owner=s.other)
    with pytest.raises(MentorError, match="identity_conflict"):
        s.links.confirm(second["id"], s.other.id, code)
    with s.store.transaction() as db:
        assert s.store.lookup(db, "principal", key("feishu", "tenant-test", "tenant-user")) is None


def test_subject_change_is_rejected_and_first_verified_subject_can_be_added(linking):
    s = linking
    request, _, proof = prepare(s, message=s.message.model_copy(update={"subject": ""}))
    s.links.confirm(request["id"], s.owner.id, proof)
    assert s.identity.resolve(s.apps[0].id, s.message)[0].id == s.owner.id
    with pytest.raises(MentorError, match="identity_conflict"):
        s.identity.resolve(s.apps[0].id, s.message.model_copy(update={"subject": "changed"}))


def test_concurrent_claims_allow_only_one_frozen_account(linking):
    s = linking
    request = s.links.create(s.owner.id, s.apps[0].id)
    def claim(index):
        try:
            return s.links.claim(s.apps[0].id, s.message.model_copy(update={
                "text": request["command"], "external_user_id": "candidate-" + str(index)}))["status"]
        except MentorError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, range(2)))
    assert results.count("identity_link_claimed") == 1
    assert results.count("identity_link_invalid") == 1


def test_http_pairing_does_not_record_tokens_as_history_or_call_models(linking):
    s = linking
    response = s.client.post("/api/mentors/v1/identity-links", json={"application_id": s.apps[0].id})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    request = response.json()
    message = s.message.model_copy(update={"text": request["command"]})
    response = s.client.post("/api/mentors/v1/messages", json=message.model_dump(),
                            headers={"Authorization": "Bearer synthetic-app-transport"})
    assert response.status_code == 200, response.text
    proof = response.json()["text"].splitlines()[0].removeprefix("核对码：")
    assert s.client.post("/api/mentors/v1/identity-links/" + request["id"] + "/confirm", json={"code": proof}).status_code == 200
    data = s.client.get("/api/mentors/v1/connections").json()["items"]
    assert data[0]["connected"] and not data[1]["connected"]
    assert "open-synthetic" not in json.dumps(data)
    with s.store.transaction() as db:
        dump = "\n".join(db.iterdump())
        assert request["command"].split()[1] not in dump
        assert "核对码" not in dump
        assert db.execute("SELECT count(*) FROM mentor_keys WHERE kind='incoming'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM mentor_records WHERE kind IN ('conversation','artifact')").fetchone()[0] == 0


def test_unknown_and_removed_apps_cannot_be_linked(linking):
    s = linking
    request, _, proof = prepare(s)
    s.links.applications = frozenset()
    assert not s.links.connections(s.owner.id)["items"]
    with pytest.raises(MentorError, match="identity_link_application_unavailable"):
        s.links.confirm(request["id"], s.owner.id, proof)


def test_owner_only_routes_and_no_owner_override_in_payload(linking):
    s = linking
    for token in ["", "synthetic-app-transport"]:
        assert s.client.get("/api/mentors/v1/connections", headers={"Authorization": token}).status_code == 401
    response = s.client.post("/api/mentors/v1/identity-links", json={
        "application_id": s.apps[0].id, "owner_id": s.other.id})
    assert response.status_code == 422 and "owner_id" not in response.text


def test_sdk_normalization_allows_missing_user_id_but_checks_application_and_tenant(linking):
    pytest.importorskip("lark_oapi")
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    from riji_agent.mentors.feishu import normalize
    app = linking.apps[0]
    raw = {"schema": "2.0", "header": {"event_id": "event", "app_id": app.external_id}, "event": {
        "sender": {"sender_type": "user", "tenant_key": app.tenant, "sender_id": {"open_id": "open-synthetic"}},
        "message": {"message_id": "message", "chat_id": "private", "chat_type": "p2p",
                    "message_type": "text", "content": json.dumps({"text": "Synthetic question"})}}}
    event = P2ImMessageReceiveV1(raw)
    assert normalize(event, app).subject == ""
    event.header.app_id = "wrong"
    with pytest.raises(MentorError, match="feishu_identity_unverified"):
        normalize(event, app)
    event.header.app_id = app.external_id
    event.event.sender.tenant_key = "wrong"
    with pytest.raises(MentorError, match="feishu_identity_unverified"):
        normalize(event, app)
    event.event.sender.tenant_key = app.tenant
    event.event.sender.sender_type = "app"
    with pytest.raises(MentorError, match="feishu_identity_unverified"):
        normalize(event, app)


def test_receiver_loads_secrets_from_private_runtime_file(tmp_path, monkeypatch):
    import os
    if os.name != "posix":
        pytest.skip("Receiver requires POSIX private credential files and locks.")
    from riji_agent.mentors.configuration import ApplicationConfig, MentorConfig, UserConfig
    from riji_agent.mentors.receiver import receiver_credentials
    app = ApplicationConfig(external_id="synthetic-app", tenant="synthetic-tenant", persona_id="gentle_reviewer",
        secret_env="SYNTHETIC_APP_SECRET", transport_token_env="SYNTHETIC_TRANSPORT_TOKEN")
    monkeypatch.delenv(app.secret_env, raising=False)
    monkeypatch.delenv(app.transport_token_env, raising=False)
    credentials = tmp_path / "private-credentials.env"
    credentials.write_text("SYNTHETIC_APP_SECRET=" + "synthetic-secret-" * 3
                           + "\nSYNTHETIC_TRANSPORT_TOKEN=" + "synthetic-transport-" * 3 + "\n")
    credentials.chmod(0o600)
    config = MentorConfig(applications=(app,), users=(UserConfig(account=Account(platform="local",
        tenant="test", subject="owner"), legacy_owner_key="owner", review_token_env="SYNTHETIC_REVIEW"),),
        credentials_env_file=credentials)
    secret, token = receiver_credentials(config, app, tmp_path / "vault")
    assert secret.get_secret_value() == "synthetic-secret-" * 3
    assert token.get_secret_value() == "synthetic-transport-" * 3
    credentials.chmod(0o644)
    with pytest.raises(MentorError, match="mentor_credentials_permissions_invalid"):
        receiver_credentials(config, app, tmp_path / "vault")
