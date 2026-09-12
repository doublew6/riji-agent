"""Exercise the actual opt-in wiring and HTTP boundary with synthetic model data."""

import json
import secrets
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.mentors.api import build_router
from riji_agent.mentors.models import Application, MentorError
from riji_agent.mentors.runtime import build_runtime
from riji_agent.models.types import AssistantTurn


class SyntheticProvider:
    def __init__(self):
        self.calls = []
    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        body = json.loads(messages[-1]["content"])
        targets = [item["id"] for item in body["previous"] if item["kind"] in {"opinion", "debate"}]
        # The selected role is explicit in the system message, but targeting any
        # prior different actor is best tested through the service fixture.
        return AssistantTurn(json.dumps({"text": "Synthetic provider result", "claims": ["Synthetic choice"],
                    "responds_to": targets[:1] if body["stage"] == "debate" else [],
                    "debate_needed": False if body["stage"] == "comparison" else None,
                    "comparison_findings": [],
                    "uncertainties": ["Synthetic uncertainty"], "next_steps": ["Synthetic action"]}))


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import os
    if os.name == "nt":
        pytest.skip("Mentor runtime enrollment requires verified POSIX private-file permissions.")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    actors = ("host", "gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")
    apps = []
    for n, actor in enumerate(actors):
        name = "TEST_MENTOR_TRANSPORT_" + str(n)
        monkeypatch.setenv(name, (str(n) + "synthetic-transport-") * 3)
        apps.append({"external_id": actor, "platform": "local", "tenant": "test",
                     "persona_id": actor, "transport_token_env": name})
    monkeypatch.setenv("TEST_MENTOR_REVIEW", "synthetic-review-" * 3)
    config = tmp_path / "mentor-config.json"
    config.write_text(json.dumps({"applications": apps, "users": [{"account": {"platform": "local",
        "tenant": "test", "subject": "synthetic-user"}, "legacy_owner_key": "synthetic-owner",
        "review_token_env": "TEST_MENTOR_REVIEW"}]}))
    config.chmod(0o600)
    settings = SimpleNamespace(mentors_enabled=True, mentors_config_path=config, journal_root=tmp_path / "vault",
                               data_dir=tmp_path / "data", port=8765, feishu_app_id=None)
    provider = SyntheticProvider()
    runtime = build_runtime(settings, model=provider, memory_service=None, drafts=None)
    app = FastAPI()
    app.include_router(build_router(runtime))
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + "synthetic-review-" * 3
    yield runtime, client, provider
    runtime.graph.close()


def test_optional_runtime_private_flow_uses_fixed_persona(runtime):
    runtime, client, provider = runtime
    me = client.get("/api/mentors/v1/me").json()
    chats = me["chats"]
    app = next(item for item in runtime.store.list("application", "", Application) if item.persona_id == "gentle_reviewer")
    binding = next(item for item in chats if item["application_id"] == app.id)
    response = client.post("/api/mentors/v1/conversations", json={"binding_id": binding["id"],
                "question": "Synthetic current question", "personas": ["gentle_reviewer"]})
    assert response.status_code == 200, response.text
    identifier = response.json()["id"]
    assert runtime.worker.run_one(identifier)
    assert runtime.worker.dispatcher.dispatch_one(identifier)
    assert runtime.worker.dispatcher.dispatch_one(identifier)
    view = client.get("/api/mentors/v1/conversations/" + identifier).json()
    assert view["conversation"]["status"] == "completed", view
    assert view["artifacts"][-1]["text"] == "Synthetic provider result"
    assert not provider.calls[0][1]
    assert "温柔" in provider.calls[0][0][0]["content"]


def test_http_tokens_are_separated_and_not_optional(runtime):
    runtime, client, provider = runtime
    client.headers.clear()
    assert client.get("/api/mentors/v1/conversations").status_code == 401
    transport_token = next(iter(runtime.transport_tokens))
    client.headers["Authorization"] = "Bearer " + transport_token
    assert client.get("/api/mentors/v1/conversations").status_code == 401


def test_private_credential_file_for_service_startup(tmp_path, monkeypatch):
    import os
    if os.name == "nt":
        pytest.skip("Private credentials require POSIX permissions.")
    from riji_agent.mentors.configuration import MentorConfig, credential, load_credentials
    name = "SYNTHETIC_SERVICE_TOKEN"
    monkeypatch.delenv(name, raising=False)
    secret_file = tmp_path / "mentors.env"
    secret_file.write_text(name + "=" + "synthetic-service-token-" * 3 + "\n")
    secret_file.chmod(0o600)
    config = MentorConfig(applications=[dict(external_id="host", platform="local", tenant="test",
        persona_id="host", transport_token_env=name)], users=[dict(account=dict(platform="local",
        tenant="test", subject="synthetic"), legacy_owner_key="synthetic", review_token_env=name)],
        credentials_env_file=secret_file)
    values = load_credentials(config, tmp_path / "vault")
    assert credential(name, values).get_secret_value() == "synthetic-service-token-" * 3
    monkeypatch.setenv(name, "synthetic-environment-token-" * 3)
    assert credential(name, values).get_secret_value() == "synthetic-environment-token-" * 3
    secret_file.chmod(0o644)
    with pytest.raises(MentorError, match="mentor_credentials_permissions_invalid"):
        load_credentials(config, tmp_path / "vault")
    secret_file.chmod(0o600)
    with pytest.raises(MentorError, match="mentor_credentials_path_invalid"):
        load_credentials(config, tmp_path)


def test_roundtable_local_adapter_uses_same_service_and_requires_share(runtime):
    runtime, client, provider = runtime
    from riji_agent.mentors.models import Application
    apps = runtime.store.list("application", "", Application)
    host = next(item for item in apps if item.role == "host")
    binding = next(item for item in client.get("/api/mentors/v1/me").json()["chats"] if item["application_id"] == host.id)
    response = client.post("/api/mentors/v1/conversations", json={"binding_id": binding["id"],
        "question": "Synthetic reference question", "personas": ["gentle_reviewer", "blunt_coach"], "mode": "reference"})
    identifier = response.json()["id"]
    assert not runtime.worker.run_one(identifier)
    base = "/api/mentors/v1/conversations/" + identifier
    preview = client.get(base + "/share-preview").json()
    assert client.post(base + "/commands", json={"id": "share-one", "kind": "share", "preview_hash": "bad"}).status_code == 409
    assert client.post(base + "/commands", json={"id": "share-two", "kind": "share", "preview_hash": preview["preview_hash"]}).status_code == 200
    for _ in range(15):
        runtime.worker.run_one(identifier)
        runtime.worker.dispatcher.dispatch_one(identifier)
    view = client.get(base).json()
    assert view["conversation"]["status"] == "completed", view
    assert len(provider.calls) == 3
    assert view["conversation"]["room_id"].startswith("local-")
    from riji_agent.mentors.local_channel import LocalChannel
    # A recreated local adapter reads the same authority; remote group membership
    # is still independently reverified by its own adapter.
    restarted = LocalChannel(runtime.store)
    room_id = view["conversation"]["room_id"]
    assert restarted.inspect_room(room_id).complete
    assert client.post(base + "/commands", json={"id": "delete-room", "kind": "delete"}).status_code == 200
    with pytest.raises(MentorError, match="local_room_unavailable"):
        restarted.inspect_room(room_id)


def test_validation_errors_never_echo_private_request_text(runtime):
    _, client, _ = runtime
    response = client.post("/api/mentors/v1/conversations", json={"binding_id": "none", "question": "SENSITIVE_SYNTHETIC" * 1000})
    assert response.status_code == 422
    assert "SENSITIVE_SYNTHETIC" not in response.text


def test_local_review_page_has_no_external_requests_or_persisted_tokens():
    from riji_agent.mentors.ui import build_review_router
    app = FastAPI()
    app.include_router(build_review_router())
    response = TestClient(app).get("/admin/mentors")
    assert response.status_code == 200
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "localStorage" not in response.text and "sessionStorage" not in response.text
    assert "innerHTML" not in response.text
    assert "sha256-" in response.headers["content-security-policy"]


def test_transfer_requires_named_preview_and_new_roundtable_share(runtime):
    from riji_agent.mentors.models import Artifact
    runtime, client, _ = runtime
    me = client.get("/api/mentors/v1/me").json()
    def binding_for(actor):
        app = next(item for item in me["applications"] if item["persona_id"] == actor)
        return next(item["id"] for item in me["chats"] if item["application_id"] == app["id"])
    created = client.post("/api/mentors/v1/conversations", json={"binding_id": binding_for("gentle_reviewer"),
        "question": "Synthetic original", "personas": ["gentle_reviewer"]}).json()
    runtime.worker.run_one(created["id"])
    item = next(item for item in runtime.store.list("artifact", created["id"], Artifact) if item.kind == "followup")
    preview = client.post("/api/mentors/v1/transfers/preview", json={"origin_id": created["id"],
        "artifact_ids": [item.id], "personas": ["gentle_reviewer", "blunt_coach"]}).json()
    target = client.post("/api/mentors/v1/conversations", json={"binding_id": binding_for("host"),
        "question": "Synthetic second question", "personas": ["gentle_reviewer", "blunt_coach"], "mode": "reference"}).json()
    accepted = client.post("/api/mentors/v1/transfers/" + preview["id"] + "/accept", json={
        "fingerprint": preview["fingerprint"], "target_id": target["id"]})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "awaiting_share"
    shared = client.get("/api/mentors/v1/conversations/" + target["id"] + "/share-preview").json()
    assert any("Synthetic provider result" in item["text"] for item in shared["sources"])
    assert shared["input_revision"] == 2
    assert not runtime.worker.run_one(target["id"])


def _linked_feishu_app(runtime, client, actor):
    from riji_agent.mentors.models import Envelope
    app = runtime.identity.register_application(Application(platform="feishu", tenant="synthetic-tenant",
        external_id="synthetic-feishu-" + actor, persona_id=actor, role="host" if actor == "host" else "mentor"))
    runtime.identity_links.applications = runtime.identity_links.applications | {app.id}
    runtime.service.policy.channel.applications[app.id] = app
    token = secrets.token_urlsafe(32)
    runtime.transport_tokens[token] = app.id
    headers = {"Authorization": "Bearer " + token}
    link = client.post("/api/mentors/v1/identity-links", json={"application_id": app.id}).json()
    message = Envelope(delivery_id="pair-event", message_id="pair-message", external_user_id="open-" + actor,
        subject="tenant-user", external_chat_id="private-" + actor, chat_type="p2p", text=link["command"])
    claim = client.post("/api/mentors/v1/messages", json=message.model_dump(), headers=headers)
    assert claim.status_code == 200, claim.text
    code = claim.json()["text"].splitlines()[0].removeprefix("核对码：")
    assert client.post("/api/mentors/v1/identity-links/" + link["id"] + "/confirm", json={"code": code}).status_code == 200
    return app, headers, message


def test_linked_feishu_private_dialogues_share_owner_and_keep_personas_separate(runtime):
    from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
    from riji_agent.mentors.models import TransportResult
    runtime, client, provider = runtime
    memory = LongTermMemory(id="synthetic-shared-memory", content="SYNTHETIC_SHARED_FACT", user_id="synthetic-owner",
        scope=MemoryScope.SHARED, persona_id=None, status=MemoryStatus.ACTIVE,
        created_at=None, updated_at=None, metadata={})
    recalls, sent = [], []
    def retrieve(question, *, user_id, persona_id):
        recalls.append((user_id, persona_id))
        return SimpleNamespace(shared=(memory,), persona=())
    runtime.service.policy.sources.primary.service = SimpleNamespace(
        retrieve=retrieve, backend=SimpleNamespace(get=lambda _: memory), journal=None)
    def send(delivery, text):
        sent.append((delivery.application_id, delivery.chat_id, text))
        return TransportResult(status="sent", message_id="synthetic-sent-" + delivery.id)
    runtime.service.policy.channel.adapters["feishu"] = SimpleNamespace(send=send)
    for actor in ("gentle_reviewer", "blunt_coach"):
        app, headers, message = _linked_feishu_app(runtime, client, actor)
        payload = message.model_copy(update={"delivery_id": "question-event", "message_id": "question-message",
            "text": "SYNTHETIC_PRIVATE_" + actor})
        result = client.post("/api/mentors/v1/messages", json=payload.model_dump(), headers=headers)
        assert result.status_code == 200, result.text
        identifier = result.json()["conversation_id"]
        assert runtime.worker.run_one(identifier)
        assert runtime.worker.dispatcher.dispatch_one(identifier)
        assert sent[-1][:2] == (app.id, message.external_chat_id)
    assert recalls == [("synthetic-owner", "gentle_reviewer"), ("synthetic-owner", "blunt_coach")]
    assert all("SYNTHETIC_SHARED_FACT" in json.dumps(call[0]) for call in provider.calls)
    assert "SYNTHETIC_PRIVATE_gentle_reviewer" not in json.dumps(provider.calls[-1][0])
    assert len(provider.calls) == 2


def test_linked_feishu_host_cannot_bypass_group_gate_via_local_principal(runtime):
    runtime, client, provider = runtime
    _, headers, message = _linked_feishu_app(runtime, client, "host")
    message = message.model_copy(update={"delivery_id": "roundtable-event", "message_id": "roundtable-message",
        "text": "/圆桌参考 gentle_reviewer,blunt_coach | Synthetic question"})
    result = client.post("/api/mentors/v1/messages", json=message.model_dump(), headers=headers)
    assert result.status_code == 409
    assert result.json()["detail"] == "feishu_roundtable_capability_not_verified"
    assert not provider.calls
    assert not client.get("/api/mentors/v1/conversations").json()["items"]
