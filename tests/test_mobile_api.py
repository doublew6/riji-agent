"""Synthetic composed device boundary; no network model or production state."""

import json
import threading
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.serialized import SerializedMemoryBackend
from riji_agent.memory.service import MemoryService
from riji_agent.mobile.api import build_router
from riji_agent.mobile.memory import memory_version
from riji_agent.mobile.service import MobileService
from riji_agent.mobile.store import MobileStore
from riji_agent.mentors.models import Application, ChatBinding, Conversation, Delivery
from test_mem0_long_term_memory import FakeBackend
from test_mentor_runtime import runtime as mentor_runtime


def record(identifier="memory-one", owner="synthetic-owner"):
    return LongTermMemory(identifier, "Synthetic remembered preference", owner, MemoryScope.SHARED,
                          None, MemoryStatus.ACTIVE, None, None, {"privacy": "cloud"})


@pytest.fixture
def mobile(mentor_runtime, tmp_path):
    runtime, _, provider = mentor_runtime
    backend = SerializedMemoryBackend(FakeBackend([record(), record("other-memory", "another-owner")]),
                                      tmp_path / "memory-mutations.lock")
    memory = MemoryService(backend, MemoryOperationsStore(tmp_path / "operations.sqlite3"), None)
    service = MobileService(MobileStore(tmp_path / "mobile.sqlite3"), runtime, memory)
    app = FastAPI()
    app.include_router(build_router(service))
    client = TestClient(app)
    principal = next(iter(runtime.user_tokens.values()))
    code = service.store.pairing(principal)
    paired = client.post("/mobile/v1/pair", json={"code": code, "device_name": "Synthetic phone"}).json()
    client.headers["Authorization"] = "Bearer " + paired["device_token"]
    return service, client, provider, paired


def post(client, path, **body):
    response = client.post("/mobile/v1/" + path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def drain(service, identifier):
    for _ in range(20):
        ran = service.runtime.worker.run_one(identifier)
        sent = service.runtime.worker.dispatcher.dispatch_one(identifier)
        if not ran and not sent:
            break
    else:
        pytest.fail("Synthetic mentor did not terminate")


def test_pairing_single_use_expiration_token_separation_and_hashes(mobile):
    service, client, _, paired = mobile
    principal = next(iter(service.runtime.user_tokens.values()))
    code = service.store.pairing(principal)
    result = post(client, "pair", code=code, device_name="Other synthetic phone")
    assert len(result["device_token"]) >= 43
    assert client.post("/mobile/v1/pair", json={"code": code, "device_name": "Replay"}).status_code == 401
    expired = service.store.pairing(principal, lifetime=-1)
    assert client.post("/mobile/v1/pair", json={"code": expired, "device_name": "Expired"}).status_code == 401
    database = service.store.path.read_bytes()
    assert code.encode() not in database and result["device_token"].encode() not in database
    assert service.store.path.stat().st_mode & 0o777 == 0o600
    client.headers["Authorization"] = "Bearer " + next(iter(service.runtime.user_tokens))
    assert client.get("/mobile/v1/capabilities").status_code == 401


def test_native_conversation_reuses_worker_and_never_uses_feishu(mobile):
    service, client, provider, _ = mobile
    result = post(client, "mentors/gentle_reviewer/messages", operation_id="message-one", text="Synthetic question")
    assert result["state"] == "completed"
    identifier = result["result"]["conversation_id"]
    drain(service, identifier)
    view = client.get("/mobile/v1/mentors/gentle_reviewer/conversation").json()
    assert view["status"] == "completed"
    assert [item["actor"] for item in view["messages"]] == ["user", "gentle_reviewer"]
    assert view["messages"][-1]["text"] == "Synthetic provider result"
    assert len(provider.calls) == 1
    apps = {item.id: item for item in service.runtime.store.list("application", "", Application)}
    assert all(apps[item.application_id].platform == "mobile" for item in
               service.runtime.store.list("delivery", identifier, Delivery))
    assert client.get("/mobile/v1/mentors/blunt_coach/conversation").json()["status"] == "empty"
    second = post(client, "mentors/gentle_reviewer/messages", operation_id="message-two", text="Synthetic followup")
    assert second["result"]["conversation_id"] == identifier
    drain(service, identifier)
    assert len(client.get("/mobile/v1/mentors/gentle_reviewer/conversation").json()["messages"]) == 4
    assert "Synthetic followup" in json.dumps(provider.calls[-1])


def test_operation_replay_conflict_and_cross_device_isolation(mobile):
    service, client, provider, _ = mobile
    body = dict(operation_id="stable-one", text="Synthetic question")
    first = post(client, "mentors/gentle_reviewer/messages", **body)
    assert post(client, "mentors/gentle_reviewer/messages", **body) == first
    assert client.post("/mobile/v1/mentors/blunt_coach/messages", json=body).status_code == 409
    assert client.post("/mobile/v1/mentors/gentle_reviewer/messages", json=dict(body, text="Changed")).status_code == 409
    second = post(client, "pair", code=service.store.pairing(next(iter(service.runtime.user_tokens.values()))),
                  device_name="Other phone")
    client.headers["Authorization"] = "Bearer " + second["device_token"]
    assert client.get("/mobile/v1/operations/stable-one").status_code == 404
    assert client.get("/mobile/v1/mentors/gentle_reviewer/conversation").json()["status"] == "empty"


def test_revoked_device_blocks_queued_and_restart_mobile_work(mobile):
    service, client, provider, paired = mobile
    receipt = post(client, "mentors/gentle_reviewer/messages", operation_id="queued-one", text="Synthetic question")
    identifier = receipt["result"]["conversation_id"]
    post(client, "device/revoke", operation_id="revoke-one")
    assert client.get("/mobile/v1/capabilities").status_code == 401
    service.runtime.worker.run_one(identifier)
    assert not provider.calls
    assert service.runtime.store.read("conversation", identifier, Conversation).status == "interrupted"
    # Simulate a restart with mobile disabled: default policy guard is absent.
    service.runtime.service.policy.device_guard = None
    current = service.runtime.store.read("conversation", identifier, Conversation)
    with service.runtime.store.transaction() as db:
        service.runtime.store.put(db, "conversation", current.model_copy(update={"status": "queued"}), current.owner_id)
    service.runtime.worker.run_one(identifier)
    assert not provider.calls


def test_revocation_after_generation_blocks_pending_delivery(mobile):
    service, client, _, paired = mobile
    identifier = post(client, "mentors/gentle_reviewer/messages", operation_id="reply-one", text="Synthetic question")["result"]["conversation_id"]
    assert service.runtime.worker.run_one(identifier)
    service.store.revoke(paired["device_id"])
    service.runtime.worker.dispatcher.dispatch_one(identifier)
    assert not any(item.status == "sent" for item in service.runtime.store.list("delivery", identifier, Delivery))


def test_memory_list_is_owner_scoped_and_actions_versioned(mobile):
    service, client, _, _ = mobile
    rows = client.get("/mobile/v1/memories").json()["items"]
    assert [item["id"] for item in rows] == ["memory-one"]
    assert "user_id" not in rows[0] and "metadata" not in rows[0]
    assert client.get("/mobile/v1/memories/other-memory").status_code == 404
    result = post(client, "memories/memory-one/actions", operation_id="correct-one", action="update",
                  expected_version=rows[0]["version"], content="Corrected synthetic preference")
    assert result["state"] == "completed"
    repeated = post(client, "memories/memory-one/actions", operation_id="correct-one", action="update",
                    expected_version=rows[0]["version"], content="Corrected synthetic preference")
    assert repeated == result
    stale = post(client, "memories/memory-one/actions", operation_id="correct-two", action="update",
                 expected_version=rows[0]["version"], content="Stale text")
    assert stale["state"] == "failed" and stale["error"] == "memory_version_changed"
    assert service.memory.service.backend.get("memory-one").content == "Corrected synthetic preference"


def test_unknown_after_side_effect_is_never_replayed(mobile, monkeypatch):
    service, client, _, _ = mobile
    version = client.get("/mobile/v1/memories/memory-one").json()["version"]
    original = service.memory.service.update_memory
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(1)
        original(*args, **kwargs)
        raise RuntimeError("SENSITIVE_INTERNAL_PATH")
    monkeypatch.setattr(service.memory.service, "update_memory", interrupted)
    body = dict(operation_id="unknown-one", action="update", expected_version=version, content="Retained correction")
    first = post(client, "memories/memory-one/actions", **body)
    assert first["state"] == "unknown" and "SENSITIVE" not in json.dumps(first)
    assert post(client, "memories/memory-one/actions", **body) == first
    assert client.get("/mobile/v1/operations/unknown-one").json() == first
    assert len(calls) == 1


def test_interrupted_intent_recovery_does_not_apply_effect(mobile):
    service, client, _, paired = mobile
    service.store.begin(paired["device_id"], "interrupted-one", "memory_action", {"synthetic": True})
    restarted = MobileStore(service.store.path)
    restarted.recover()
    result = client.get("/mobile/v1/operations/interrupted-one").json()
    assert result["state"] == "unknown"
    assert service.memory.service.backend.get("memory-one").content == record().content


def test_body_validation_does_not_echo_private_input(mobile):
    _, client, _, _ = mobile
    response = client.post("/mobile/v1/mentors/gentle_reviewer/messages", json={"operation_id": "bad",
                "text": "SENSITIVE_SYNTHETIC" * 1000})
    assert response.status_code == 422 and "SENSITIVE_SYNTHETIC" not in response.text
    assert client.post("/mobile/v1/pair", content=b"x" * 65537).status_code == 413


def test_shared_mutation_guard_rechecks_after_existing_writer(mobile):
    service, client, _, paired = mobile
    memory = service.memory.service
    old = memory_version(memory.backend.get("memory-one"))
    entered, release = threading.Event(), threading.Event()
    def existing_writer():
        with memory.backend.mutation_guard():
            memory.update_memory("memory-one", user_id="synthetic-owner", content="Newer Review correction")
            entered.set()
            assert release.wait(3)
    writer = threading.Thread(target=existing_writer)
    writer.start()
    assert entered.wait(3)
    result = []
    def mobile_writer():
        result.append(post(client, "memories/memory-one/actions", operation_id="concurrent-one", action="update",
                           expected_version=old, content="Stale mobile correction"))
    phone = threading.Thread(target=mobile_writer)
    phone.start()
    release.set()
    writer.join(3); phone.join(3)
    assert not writer.is_alive() and not phone.is_alive()
    assert result[0]["error"] == "memory_version_changed"
    assert memory.backend.get("memory-one").content == "Newer Review correction"


def test_privacy_controls_use_current_bound_engine(mobile, tmp_path):
    from test_journal_memory import runtime
    from riji_agent.memory.journal_backend import JournalEvidenceBackend
    service, client, _, _ = mobile
    (tmp_path / "journal-scope").mkdir()
    engine, _ = runtime(tmp_path / "journal-scope")
    engine.policy = replace(engine.policy, user_id="synthetic-owner")
    service.memory.service.journal = engine
    current = client.get("/mobile/v1/privacy").json()
    assert current["available"] and not current["valid"]
    assert set(current["permissions"]) == {"history", "incremental", "organization", "recall"}
    assert str(tmp_path) not in json.dumps(current)
    invalid = post(client, "privacy/actions", operation_id="stale-consent", action="grant", binding="0" * 64,
                   acknowledged=True, permissions=dict.fromkeys(current["permissions"], True))
    assert invalid["state"] == "failed"
    result = post(client, "privacy/actions", operation_id="grant-consent", action="grant", binding=current["binding"],
                  acknowledged=True, permissions=dict.fromkeys(current["permissions"], True))
    assert result["state"] == "completed"
    assert engine.privacy.status()["valid"] and engine.store.get_control("paused") == "1"
    assert post(client, "privacy/actions", operation_id="resume-consent", action="resume",
                binding=current["binding"])["state"] == "completed"
    assert engine.store.get_control("paused") == "0"
    assert post(client, "privacy/actions", operation_id="revoke-consent", action="revoke",
                binding=current["binding"])["state"] == "completed"
    assert not engine.privacy.status()["valid"]


def journal_mobile(service, tmp_path):
    from riji_agent.memory.journal_backend import JournalEvidenceBackend
    from riji_agent.memory.journal_engine import JournalMemoryEngine
    from riji_agent.memory.journal_store import JournalMemoryStore
    from riji_agent.memory.journal_types import JournalMemoryPolicy
    from riji_agent.mentors.sources import MemorySources
    from test_journal_memory import JournalModel, write_note
    root = tmp_path / "scoped-vault"
    root.mkdir()
    policy = JournalMemoryPolicy(root, "synthetic-owner", ("🧠 Notes",),
                                 mentors=("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"))
    raw = service.memory.service.backend
    engine = JournalMemoryEngine(policy, JournalMemoryStore(tmp_path / "journal-state.sqlite3"), raw, JournalModel())
    engine.privacy.grant(engine.privacy.binding, dict.fromkeys(("history", "incremental", "organization", "recall"), True))
    service.memory.service.journal = engine
    service.memory.service.backend = JournalEvidenceBackend(raw, engine)
    service.runtime.service.policy.sources.primary = MemorySources(service.memory.service, service.runtime.identity.personas)
    source = write_note(root)
    engine.scan()
    assert engine.process_next()
    return engine, source


def test_journal_evidence_invalidates_delivered_answer_and_deletion_suppresses(mobile, tmp_path):
    service, client, _, _ = mobile
    engine, source = journal_mobile(service, tmp_path)
    records = client.get("/mobile/v1/memories").json()["items"]
    fact = next(item for item in records if item["sources"])
    assert fact["sources"][0]["relative_path"] == "daily/2026/08/2026-08-01.md"
    assert str(tmp_path) not in json.dumps(fact)
    identifier = post(client, "mentors/gentle_reviewer/messages", operation_id="source-message", text="Synthetic question")["result"]["conversation_id"]
    drain(service, identifier)
    visible = client.get("/mobile/v1/mentors/gentle_reviewer/conversation").json()["messages"][-1]
    assert visible["text"] and fact["id"] in visible["source_ids"]
    source.write_text(source.read_text().replace("准备转岗", "决定暂缓转岗"))
    changed = client.get("/mobile/v1/memories/" + fact["id"]).json()
    assert changed["state"] == "source_invalid" and changed["version"] != fact["version"]
    hidden = client.get("/mobile/v1/mentors/gentle_reviewer/conversation").json()["messages"][-1]
    assert hidden["text"] == "" and hidden["unavailable"] == "source_changed_or_revoked"
    deleted = post(client, "memories/" + fact["id"] + "/actions", operation_id="delete-fact",
                   expected_version=changed["version"], action="delete", confirmation="DELETE")
    assert deleted["state"] == "completed"
    assert source.exists() and engine.store.suppressed(memory_id=fact["id"])
    assert client.get("/mobile/v1/memories/" + fact["id"]).status_code == 404


def test_restore_gate_and_permission_mutation_preserve_recall_boundary(mobile, tmp_path):
    service, client, _, _ = mobile
    engine, _ = journal_mobile(service, tmp_path)
    fact = client.get("/mobile/v1/memories/memory-one").json()
    engine.store.set_control("restore_in_progress", "1")
    blocked = post(client, "memories/memory-one/actions", operation_id="restore-blocked",
                   expected_version=fact["version"], action="update", content="Not applied")
    assert blocked["state"] == "failed" and blocked["error"] == "memory_restore_in_progress"
    engine.store.set_control("restore_in_progress", "")
    restricted = post(client, "memories/memory-one/actions", operation_id="permission-one",
                      expected_version=fact["version"], action="permission", permission="local")
    assert restricted["state"] == "completed"
    assert client.get("/mobile/v1/memories/memory-one").json()["privacy"] == "local"
    identifier = post(client, "mentors/gentle_reviewer/messages", operation_id="after-restrict",
                      text="Synthetic question")["result"]["conversation_id"]
    from riji_agent.mentors.models import Source
    conversation = service.runtime.store.read("conversation", identifier, Conversation)
    background = [service.runtime.store.read("source", source, Source) for source in conversation.source_ids]
    assert not any(source.id == "memory-one" for source in background)


def test_memory_pagination_detects_changed_snapshot(mobile):
    service, client, _, _ = mobile
    raw = service.memory.service.backend.backend
    raw.records.update({"extra-" + str(n): record("extra-" + str(n)) for n in range(110)})
    page = client.get("/mobile/v1/memories").json()
    assert len(page["items"]) == 100 and page["next_cursor"]
    next_page = client.get("/mobile/v1/memories", params={"cursor": page["next_cursor"]}).json()
    assert len(next_page["items"]) == 11 and next_page["next_cursor"] is None
    service.memory.service.backend.update("memory-one", content="New content")
    assert client.get("/mobile/v1/memories", params={"cursor": page["next_cursor"]}).status_code == 409


def test_failure_and_unknown_receipts_do_not_expose_backend_errors(mobile, monkeypatch):
    service, client, _, _ = mobile
    def offline(*args, **kwargs):
        from riji_agent.memory.backend import MemoryBackendError
        raise MemoryBackendError("mem0_unavailable")
    monkeypatch.setattr(service.memory.service.backend.backend, "get", offline)
    response = client.get("/mobile/v1/memories/memory-one")
    assert response.status_code == 503 and response.json()["detail"] == "memory_unavailable"
    assert response.headers["cache-control"] == "no-store"


def test_receipt_404_allows_explicit_same_id_send_and_delayed_duplicate(mobile):
    service, client, provider, _ = mobile
    assert client.get("/mobile/v1/operations/lost-before-arrival").status_code == 404
    body = {"operation_id": "lost-before-arrival", "text": "Synthetic question"}
    retried = post(client, "mentors/gentle_reviewer/messages", **body)
    delayed = post(client, "mentors/gentle_reviewer/messages", **body)
    assert delayed == retried
    drain(service, retried["result"]["conversation_id"])
    assert len(provider.calls) == 1


def test_memory_deletion_retains_completed_receipt(mobile):
    service, client, _, _ = mobile
    version = client.get("/mobile/v1/memories/memory-one").json()["version"]
    body = dict(operation_id="delete-with-receipt", expected_version=version, action="delete", confirmation="DELETE")
    receipt = post(client, "memories/memory-one/actions", **body)
    assert receipt["state"] == "completed"
    assert client.get("/mobile/v1/memories/memory-one").status_code == 404
    assert client.get("/mobile/v1/operations/delete-with-receipt").json() == receipt
    assert post(client, "memories/memory-one/actions", **body) == receipt


def test_stale_stop_is_urgent_but_resume_requires_current_revision(mobile):
    from riji_agent.mentors.models import Command
    service, client, _, paired = mobile
    identifier = post(client, "mentors/gentle_reviewer/messages", operation_id="stop-target",
                      text="Synthetic question")["result"]["conversation_id"]
    owner = service.store.device(paired["device_id"])["principal"]
    service.runtime.service.apply(Command(id="synthetic-supplement", principal_id=owner,
        conversation_id=identifier, kind="supplement", expected_revision=1, text="Newer synthetic background"))
    assert service.runtime.store.read("conversation", identifier, Conversation).input_revision == 2
    stopped = post(client, "mentors/gentle_reviewer/control", operation_id="urgent-stop", action="stop", expected_revision=1)
    assert stopped["state"] == "completed"
    assert service.runtime.store.read("conversation", identifier, Conversation).status == "stopped"
    resumed = post(client, "mentors/gentle_reviewer/control", operation_id="stale-resume", action="resume", expected_revision=1)
    assert resumed["state"] == "failed" and resumed["error"] == "conversation_version_changed"
