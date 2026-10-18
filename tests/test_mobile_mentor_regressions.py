"""Current mobile recall and complete byte-bounded history with synthetic data."""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.models import Artifact, Conversation, Delivery, Source
from riji_agent.mentors.sources import MemorySources
from riji_agent.models.types import AssistantTurn
from riji_agent.mobile.proxy import MAX_RESPONSE, admitted, create_proxy_app
from riji_agent.mobile.service import MAX_CONVERSATION_BYTES, history_page
from test_mobile_api import drain, mentor_runtime, mobile, post  # noqa: F401
from test_private_mentor_context import WireProvider, last_payload

PERSONA = "gentle_reviewer"
CHAT = "/mobile/v1/mentors/" + PERSONA + "/conversation"


def connected(mobile):
    service, client, _, paired = mobile
    primary = MemorySources(service.memory.service, service.runtime.identity.personas)
    service.runtime.service.policy.sources.primary = primary
    provider = WireProvider()
    service.runtime.worker.generator = ModelGeneration(provider, service.runtime.identity.personas)
    return service, client, provider, paired


def send(client, operation, text="Synthetic current user condition."):
    receipt = post(client, "mentors/" + PERSONA + "/messages", operation_id=operation, text=text)
    assert receipt["state"] == "completed", receipt
    return receipt["result"]["conversation_id"]


def mutate(client, action, **fields):
    current = client.get("/mobile/v1/memories/memory-one").json()
    receipt = post(client, "memories/memory-one/actions", operation_id="change-" + action,
                   action=action, expected_version=current["version"], **fields)
    assert receipt["state"] == "completed", receipt


def test_corrected_memory_keeps_same_chat_and_original_user_history(mobile, monkeypatch):
    service, client, provider, _ = connected(mobile)
    primary, queries = service.runtime.service.policy.sources.primary, []
    background = primary.background

    def selecting(principal, conversation):
        queries.append(conversation.question)
        return background(principal, conversation)

    monkeypatch.setattr(primary, "background", selecting)
    identifier = send(client, "first")
    drain(service, identifier)
    original = client.get(CHAT).json()["messages"]
    assert original[-1]["source_ids"] == ["memory-one"]
    mutate(client, "update", content="Synthetic corrected current preference.")
    assert send(client, "second", "Use the corrected preference for this question.") == identifier
    drain(service, identifier)
    payload = last_payload(provider)
    assert payload["question"] == "Use the corrected preference for this question."
    assert queries[-1] == payload["question"]
    assert [source["text"] for source in payload["background"]] == ["Synthetic corrected current preference."]
    assert [item["actor"] for item in payload["previous"]] == ["user", "user"]
    assert payload["previous"][0]["text"] == original[0]["text"]
    assert original[-1]["id"] not in [item["id"] for item in payload["previous"]]
    view = client.get(CHAT).json()
    assert view["conversation_id"] == identifier and view["status"] == "completed"
    assert view["messages"][1]["text"] == ""
    assert view["messages"][1]["unavailable"] == "source_changed_or_revoked"
    assert view["messages"][-1]["source_ids"] == ["memory-one"]
    send(client, "third", "Continue with the corrected answer.")
    drain(service, identifier)
    assert [item["actor"] for item in last_payload(provider)["previous"]] == ["user", "user", PERSONA, "user"]
    assert len(provider.messages) == 3


def test_history_cursor_expires_when_old_output_becomes_unavailable(mobile, monkeypatch):
    import riji_agent.mobile.service as module
    service, client, _, _ = connected(mobile)
    identifier = send(client, "first")
    drain(service, identifier)
    monkeypatch.setattr(module, "MAX_CONVERSATION_MESSAGES", 1)
    page = client.get(CHAT).json()
    assert page["next_cursor"]
    mutate(client, "permission", permission="none")
    changed = client.get(CHAT, params={"cursor": page["next_cursor"]})
    assert changed.status_code == 409 and changed.json()["detail"] == "conversation_page_changed"
    fresh = client.get(CHAT).json()
    assert fresh["history_version"] != page["history_version"]
    assert fresh["messages"][0]["text"] == "" and fresh["messages"][0]["unavailable"]


@pytest.mark.parametrize("action,fields", [
    ("permission", {"permission": "none"}), ("permission", {"permission": "local"}),
    ("delete", {"confirmation": "DELETE"}), ("archive", {}),
])
def test_restricted_or_removed_memory_never_reenters_continued_context(mobile, action, fields):
    service, client, provider, _ = connected(mobile)
    identifier = send(client, "first")
    drain(service, identifier)
    original = client.get(CHAT).json()["messages"][-1]
    mutate(client, action, **fields)
    for operation in ("second", "third"):
        assert send(client, operation) == identifier
        drain(service, identifier)
        payload = last_payload(provider)
        assert payload["background"] == []
        assert original["id"] not in [item["id"] for item in payload["previous"]]
        assert "Synthetic remembered preference" not in json.dumps(payload)
    view = client.get(CHAT).json()
    assert view["status"] == "completed" and len(provider.messages) == 3
    assert view["messages"][1]["text"] == "" and view["messages"][1]["unavailable"]
    assert all(not item["source_ids"] for item in view["messages"][2:])


@pytest.mark.parametrize("boundary", ["model", "delivery"])
def test_retained_history_dependencies_revalidate_without_becoming_current_background(mobile, monkeypatch, boundary):
    service, client, provider, _ = connected(mobile)
    identifier = send(client, "first")
    drain(service, identifier)
    primary = service.runtime.service.policy.sources.primary
    monkeypatch.setattr(primary, "background", lambda principal, conversation: ())
    assert send(client, "second") == identifier
    current = service.runtime.store.read("conversation", identifier, Conversation)
    assert not current.source_ids and current.history_source_ids
    if boundary == "model":
        provider.before_guard = lambda: service.memory.service.backend.update("memory-one", metadata={"privacy": "none"})
        drain(service, identifier)
        assert len(provider.messages) == 1
        provider.before_guard = lambda: None
    else:
        assert service.runtime.worker.run_one(identifier)
        assert last_payload(provider)["background"] == []
        assert any(item["actor"] == PERSONA for item in last_payload(provider)["previous"])
        mutate(client, "permission", permission="none")
        drain(service, identifier)
        assert len(provider.messages) == 2
    assert client.get(CHAT).json()["status"] == "interrupted"
    assert sum(item.status == "sent" for item in service.runtime.store.list("delivery", identifier, Delivery)) == 1
    assert send(client, "after-interrupted") == identifier
    drain(service, identifier)
    assert client.get(CHAT).json()["status"] == "completed"
    assert all(item["actor"] == "user" for item in last_payload(provider)["previous"])


def test_delete_after_correction_removes_current_and_historical_source_snapshots(mobile):
    service, client, _, _ = connected(mobile)
    identifier = send(client, "before-correction")
    drain(service, identifier)
    original = service.runtime.store.read("conversation", identifier, Conversation)
    mutate(client, "update", content="Synthetic replacement memory.")
    assert send(client, "after-correction") == identifier
    drain(service, identifier)
    current = service.runtime.store.read("conversation", identifier, Conversation)
    assert set(original.source_ids).isdisjoint((*current.source_ids, *current.history_source_ids))
    all_sources = (*original.source_ids, *current.source_ids)
    assert all(service.runtime.store.read("source", ref, Source) for ref in all_sources)
    service.runtime.history.delete(identifier, current.owner_id, "delete-corrected-history")
    assert not service.runtime.store.list("artifact", identifier, Artifact)
    assert all(service.runtime.store.read("source", ref, Source) is None for ref in all_sources)
    assert not service.runtime.store.list("source", current.owner_id, Source)
    assert service.memory.service.backend.get("memory-one").content == "Synthetic replacement memory."


def test_delete_preserves_other_conversations_rotated_historical_sources(mobile):
    service, client, _, _ = connected(mobile)
    first_id = send(client, "first-device-initial")
    drain(service, first_id)
    principal = next(iter(service.runtime.user_tokens.values()))
    pair = post(client, "pair", code=service.store.pairing(principal), device_name="Synthetic second phone")
    other = TestClient(client.app)
    other.headers["Authorization"] = "Bearer " + pair["device_token"]
    second_id = send(other, "second-device-initial")
    drain(service, second_id)
    old_sources = service.runtime.store.read("conversation", second_id, Conversation).source_ids
    mutate(client, "update", content="Synthetic new shared source version.")
    for device_client, identifier in ((client, first_id), (other, second_id)):
        assert send(device_client, "new-version") == identifier
        drain(service, identifier)
    second = service.runtime.store.read("conversation", second_id, Conversation)
    assert set(old_sources).isdisjoint((*second.source_ids, *second.history_source_ids))
    expected_sources = set(old_sources) | set(second.source_ids)
    service.runtime.history.delete(first_id, principal, "delete-first-device-chat")
    assert all(service.runtime.store.read("source", ref, Source) for ref in expected_sources)
    assert service.runtime.store.list("artifact", second_id, Artifact)
    service.runtime.history.delete(second_id, principal, "delete-second-device-chat")
    assert all(service.runtime.store.read("source", ref, Source) is None for ref in expected_sources)


def long_first_reply(provider, monkeypatch, *, text_size, claim_size=0):
    complete = provider.complete

    def respond(messages, tools):
        result = complete(messages, tools)
        if len(provider.messages) != 1:
            return result
        body = json.loads(result.content)
        body.update(text="中" * text_size, claims=["据" * claim_size] if claim_size else [])
        return AssistantTurn(content=json.dumps(body))

    monkeypatch.setattr(provider, "complete", respond)


def test_oversized_previous_artifact_does_not_block_next_short_user_message(mobile, monkeypatch):
    service, client, provider, _ = connected(mobile)
    long_first_reply(provider, monkeypatch, text_size=12000, claim_size=6000)
    identifier = send(client, "long-answer")
    drain(service, identifier)
    first = service.runtime.store.list("artifact", identifier, Artifact)[-1]
    assert len(first.text) == 12000 and len(first.claims[0]) == 6000
    assert send(client, "short-next", "A short continuation.") == identifier
    pending = service.runtime.store.list("artifact", identifier, Artifact)[-1]
    current = service.runtime.store.read("conversation", identifier, Conversation)
    assert current.history_artifact_ids == (pending.id,)
    assert not current.history_source_ids
    drain(service, identifier)
    assert client.get(CHAT).json()["status"] == "completed" and len(provider.messages) == 2
    assert last_payload(provider)["previous"] == [pending.model_dump(mode="json")]


def test_source_excluded_by_new_question_budget_does_not_block_after_revocation(mobile, monkeypatch):
    service, client, provider, _ = connected(mobile)
    long_first_reply(provider, monkeypatch, text_size=8500)
    identifier = send(client, "history-answer")
    drain(service, identifier)
    primary = service.runtime.service.policy.sources.primary
    monkeypatch.setattr(primary, "background", lambda principal, conversation: ())
    assert send(client, "long-new-question", "问" * 10000) == identifier
    pending = service.runtime.store.list("artifact", identifier, Artifact)[-1]
    current = service.runtime.store.read("conversation", identifier, Conversation)
    assert not current.source_ids and not current.history_source_ids
    assert current.history_artifact_ids == (pending.id,)
    mutate(client, "permission", permission="none")
    drain(service, identifier)
    payload = last_payload(provider)
    assert payload["background"] == [] and payload["previous"] == [pending.model_dump(mode="json")]
    assert client.get(CHAT).json()["status"] == "completed" and len(provider.messages) == 2
    assert not service.runtime.store.list("artifact", identifier, Artifact)[-1].dependencies


def populate_long_history(service, client, count=220):
    identifier = send(client, "history-first")
    device = service.authenticate(client.headers["Authorization"][7:])
    binding = service._binding(device, PERSONA)
    with service.runtime.store.transaction() as db:
        for number in range(count):
            user = number % 2 == 0
            item = Artifact(conversation_id=identifier, actor="user" if user else PERSONA,
                kind="user" if user else "followup", input_revision=1, run_id="synthetic-old-run",
                text="中" * (10000 if user else 12000), created_at=number + 1,
                origin_kind="user_statement" if user else "ai_discussion")
            service.runtime.store.put(db, "artifact", item, identifier)
            if not user:
                delivery = Delivery(conversation_id=identifier, sequence=number + 1,
                    application_id=binding.application_id, chat_id=binding.external_chat_id,
                    artifact_id=item.id, input_revision=1, cancel_epoch=0, status="sent")
                service.runtime.store.put(db, "delivery", delivery, identifier)
    return identifier


def test_history_pages_all_whole_chinese_messages_through_actual_proxy(mobile):
    service, client, _, _ = mobile
    identifier = populate_long_history(service, client)
    expected = service.runtime.store.list("artifact", identifier, Artifact)

    def upstream(request):
        response = client.get(request.url.raw_path.decode(), headers={"Authorization": request.headers["authorization"]})
        assert len(response.content) <= MAX_CONVERSATION_BYTES < MAX_RESPONSE
        return httpx.Response(response.status_code, content=response.content, headers={"content-type": "application/json"})

    proxy = TestClient(create_proxy_app(transport=httpx.MockTransport(upstream)))
    proxy.headers["Authorization"] = client.headers["Authorization"]
    cursor, combined, versions = "", [], set()
    for _ in range(20):
        response = proxy.get(CHAT + ("?cursor=" + cursor if cursor else ""))
        assert response.status_code == 200, response.text
        assert len(response.content) <= MAX_CONVERSATION_BYTES
        page = response.json()
        assert page["history_has_more"] == bool(page["next_cursor"])
        versions.add(page["history_version"])
        combined = page["messages"] + combined
        cursor = page["next_cursor"]
        if not cursor:
            break
    else:
        pytest.fail("History pagination did not terminate")
    assert len(versions) == 1
    assert [item["id"] for item in combined] == [item.id for item in expected]
    assert [item["text"] for item in combined] == [item.text for item in expected]


def test_history_cursor_rejects_stale_foreign_and_invalid_positions(mobile):
    service, client, _, _ = mobile
    identifier = populate_long_history(service, client)
    first = client.get(CHAT).json()
    cursor = first["next_cursor"]
    assert cursor and client.get(CHAT, params={"cursor": cursor}).status_code == 200
    for value in ("invalid", *[base64.urlsafe_b64encode(json.dumps([first["history_version"], offset]).encode()).decode().rstrip("=")
                               for offset in (0, -1, 1000000, True)]):
        assert client.get(CHAT, params={"cursor": value}).status_code == 409
    assert client.get(CHAT.replace(PERSONA, "blunt_coach"), params={"cursor": cursor}).status_code == 409
    other = post(client, "pair", code=service.store.pairing(next(iter(service.runtime.user_tokens.values()))), device_name="Other phone")
    assert client.get(CHAT, params={"cursor": cursor}, headers={"Authorization": "Bearer " + other["device_token"]}).status_code == 409
    with service.runtime.store.transaction() as db:
        service.runtime.service._user_artifact(db, service.runtime.store.get(db, "conversation", identifier, Conversation), "New history")
    stale = client.get(CHAT, params={"cursor": cursor})
    assert stale.status_code == 409 and stale.json()["detail"] == "conversation_page_changed"


def test_exact_serialized_byte_limit_and_strict_proxy_cursor_queries(monkeypatch):
    import riji_agent.mobile.service as module
    view = {"conversation_id": "synthetic", "status": "completed", "revision": 1, "notice": None,
            "messages": [{"id": str(i), "text": '中文"\\\n' * 50} for i in range(2)]}
    complete = history_page(view, ("device", PERSONA), "")
    size = len(module._json_bytes(complete))
    monkeypatch.setattr(module, "MAX_CONVERSATION_BYTES", size)
    assert history_page(view, ("device", PERSONA), "")["messages"] == view["messages"]
    monkeypatch.setattr(module, "MAX_CONVERSATION_BYTES", size - 1)
    page = history_page(view, ("device", PERSONA), "")
    assert page["messages"] == view["messages"][-1:] and page["next_cursor"]
    assert admitted("GET", CHAT.encode(), b"cursor=abc_123")
    for query in (b"cursor=a&cursor=b", b"cursor=a%2Fb", b"cursor=a&target=admin", b"before=2"):
        assert not admitted("GET", CHAT.encode(), query)
    assert not admitted("POST", CHAT.encode(), b"cursor=abc")
