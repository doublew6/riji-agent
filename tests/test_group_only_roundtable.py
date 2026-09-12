"""Real core over synthetic current-room evidence; no production capability claim."""
from types import SimpleNamespace

import pytest

from test_hermes_host_group_bridge import host, post, raw_event
from test_mentor_discussions import SyntheticGenerator
from riji_agent.mentors.delivery import OutboxDispatcher
from riji_agent.mentors.group_scope import GROUP_PERSONAS
from riji_agent.mentors.models import Application, Artifact, AudienceGrant, Command, Conversation, Envelope, MentorError, Source
from riji_agent.mentors.sources import MemorySources
from riji_agent.mentors.store import key
from riji_agent.mentors.transfer import DiscussionTransfer, TransferSources
from riji_agent.mentors.worker import DiscussionWorker


@pytest.fixture
def group(host):
    apps = [host.app]
    for actor in GROUP_PERSONAS:
        apps.append(host.identity.register_application(Application(platform=host.app.platform,
            tenant=host.app.tenant, external_id="synthetic-" + actor, persona_id=actor)))
    host.evidence.snapshot = host.evidence.snapshot.model_copy(update={
        "application_ids": tuple(app.id for app in apps), "history_restricted": False, "continuity_verified": False})
    host.evidence.known_application_ids = tuple(app.id for app in apps)
    host.evidence.management_verified = True
    host.evidence.speaking_allowed = True
    host.evidence.room_attributes_verified = True
    host.evidence.member_mapping_verified = True
    host.evidence.owner_principal_id = host.owner.id
    host.evidence.member_principal_ids = (host.owner.id,)
    with host.store.transaction() as db:
        db.execute("DELETE FROM mentor_keys WHERE kind='room'")
    host.service.now = lambda: host.clock[0]
    host.service.budgets.now = host.service.now
    host.service.policy.now = host.service.now
    host.generator = SyntheticGenerator()
    host.model_worker = DiscussionWorker(host.service, host.generator, host.service.now)
    host.dispatcher = OutboxDispatcher(host.service, host.service.now)
    return host


def prepare(group):
    result = group.client.post("/api/mentors/v1/host-groups/adoptions",
        json={"expected_chat_id": "verified-room"}, headers=group.owner_headers)
    assert result.status_code == 200, result.json()
    assert result.json()["history_restricted"] is False
    assert result.json()["continuity_verified"] is False
    return result.json()


def adopt(group, text="我计划每周画画两小时，还没有开始。"):
    prepare(group)
    response = post(group, raw_event(text))
    assert response.json()["status"] == "accepted", response.json()
    with group.store.transaction() as db:
        identifier = group.store.lookup(db, "room", key(group.app.platform, group.app.tenant, "verified-room"))
    return group.service.get(identifier, group.owner.id)


def drain(group, identifier):
    for _ in range(60):
        worked = group.model_worker.run_one(identifier)
        sent = group.dispatcher.dispatch_one(identifier)
        if not worked and not sent:
            break
    else:
        pytest.fail("bounded group did not terminate")
    return group.service.get(identifier, group.owner.id)


def say(group, text, message_id):
    return post(group, raw_event(text, message={"message_id": message_id})).json()


def test_group_adopts_clean_current_room_host_then_explicit_table_runs(group):
    current = adopt(group)
    assert current.source_scope == "group_only" and current.source_ids == ()
    assert current.owner_id == group.owner.id and current.source_platform == "feishu"
    assert current.source_tenant == group.app.tenant and current.source_application_id == group.app.id
    assert current.status == "queued" and current.run_kind == "followup"
    assert drain(group, current.id).status == "completed"
    assert [call.actor for call in group.generator.calls] == ["host"]
    assert say(group, "请四位分别给我参考", "reference")["status"] == "accepted"
    first = drain(group, current.id)
    assert first.status == "completed"
    assert say(group, "请四位重新讨论", "debate")["status"] == "accepted"
    second = drain(group, current.id)
    assert second.status == "completed" and first.run_id != second.run_id
    assert any(call.stage == "debate" for call in group.generator.calls)
    assert {delivery.application_id for delivery, _ in group.sent} == set(group.evidence.known_application_ids)
    for call in group.generator.calls:
        assert call.background == ()
        assert all(item.conversation_id == current.id and item.origin_room_id == current.room_id for item in call.previous)
        assert "Synthetic problem" not in call.model_dump_json()
    grant = group.store.read("grant", second.grant_id, AudienceGrant)
    assert not grant.snapshot.history_restricted and not grant.snapshot.continuity_verified
    assert group.service.policy.freeze(second).source_ids == ()


@pytest.mark.parametrize("field", ["complete", "private", "management_restricted"])
def test_adoption_rejects_incomplete_or_unmanaged_evidence(group, field):
    group.evidence.snapshot = group.evidence.snapshot.model_copy(update={field: False})
    result = group.client.post("/api/mentors/v1/host-groups/adoptions", json={"expected_chat_id": "verified-room"}, headers=group.owner_headers)
    assert result.status_code == 409
    assert not group.generator.calls


@pytest.mark.parametrize("field,value", [("speaking_allowed", False), ("owner_principal_id", "stranger"),
    ("member_principal_ids", ("stranger",)), ("member_mapping_verified", False),
    ("known_application_ids", ()), ("human_open_ids", ("stranger",))])
def test_adoption_requires_exact_canonical_owner_and_five_controlled_bots(group, field, value):
    setattr(group.evidence, field, value)
    result = group.client.post("/api/mentors/v1/host-groups/adoptions", json={"expected_chat_id": "verified-room"}, headers=group.owner_headers)
    assert result.status_code == 409 and not group.generator.calls


def test_no_web_body_or_privately_sent_text_can_enter_group(group):
    current = adopt(group)
    before = group.store.list("artifact", current.id, Artifact)
    for suffix, body in [("messages", {"id": "web", "text": "Private web text"}),
        ("commands", {"id": "web-command", "kind": "supplement", "expected_revision": current.input_revision, "text": "Private text"})]:
        response = group.client.post(f"/api/mentors/v1/conversations/{current.id}/{suffix}", json=body, headers=group.owner_headers)
        assert response.status_code == 409 and response.json()["detail"] == "group_only_input_required"
    message = Envelope(delivery_id="private", message_id="private", external_user_id="owner-open",
        external_chat_id="host-private", chat_type="p2p", text=f"/补充 {current.id} | Private DM text")
    with pytest.raises(MentorError, match="group_only_input_required"):
        group.ingress.receive(group.app.id, message)
    assert group.store.list("artifact", current.id, Artifact) == before
    # Pure owner controls remain available through the authenticated API.
    response = group.client.post(f"/api/mentors/v1/conversations/{current.id}/commands",
        json={"id": "stop-web", "kind": "stop"}, headers=group.owner_headers)
    assert response.status_code == 200 and response.json()["status"] == "stopped"


def test_adoption_needs_new_real_message_no_old_event_or_review_body(group):
    prepare(group)
    assert post(group, raw_event(message={"create_time": "999000"})).json()["status"] == "rejected"
    assert not group.generator.calls
    body = {"expected_chat_id": "verified-room", "question": "Web private content"}
    assert group.client.post("/api/mentors/v1/host-groups/adoptions", json=body, headers=group.owner_headers).status_code == 422


def test_same_message_duplicate_keeps_one_user_input_and_one_run(group):
    current = adopt(group)
    assert post(group, raw_event("我计划每周画画两小时，还没有开始。", header={"event_id": "delivery-other"})).json()["duplicate"]
    artifacts = group.store.list("artifact", current.id, Artifact)
    assert len([item for item in artifacts if item.kind == "user"]) == 1


def test_group_sources_short_circuit_before_any_external_backend(group):
    current = adopt(group)
    poison = SimpleNamespace(retrieve=lambda *a, **k: pytest.fail("private retrieve"))
    assert MemorySources(poison, group.identity.personas).background(group.owner, current) == ()
    assert TransferSources(group.store, group.service.policy.sources).background(group.owner, current) == ()
    transfer = DiscussionTransfer(group.history)
    with pytest.raises(MentorError, match="group_only_source_violation"):
        transfer.accept("absent", group.owner.id, "anything", current.id)
    contaminated = current.model_copy(update={"source_ids": ("private-id",)})
    with pytest.raises(MentorError, match="group_only_source_violation"):
        group.service.policy.background(contaminated)


def test_foreign_artifact_is_rejected_before_source_validation_or_model(group):
    current = adopt(group)
    poisoned = Artifact(conversation_id=current.id, actor="host", kind="followup", input_revision=1,
        text="Other chat content", origin_room_id="another-room", dependencies=("private-source",), created_at=1000)
    with group.store.transaction() as db:
        group.store.put(db, "artifact", poisoned, current.id)
    assert group.model_worker.run_one(current.id)
    assert not group.generator.calls
    assert group.service.get(current.id, group.owner.id).status == "interrupted"


def test_membership_change_blocks_before_send_and_stop_still_works(group):
    current = adopt(group)
    assert group.model_worker.run_one(current.id)
    group.evidence.snapshot = group.evidence.snapshot.model_copy(update={"human_subjects": ("someone-else",)})
    assert group.dispatcher.dispatch_one(current.id)
    assert not group.sent
    assert group.service.get(current.id, group.owner.id).status == "interrupted"
    assert say(group, "停止讨论", "stop")["status"] == "accepted"
    assert group.service.get(current.id, group.owner.id).status == "stopped"


def test_group_control_notice_uses_same_mode_aware_audience_proof(group):
    current = adopt(group)
    assert group.bridge.dispatch_notice(current.id)
    assert len(group.sent) == 1 and group.sent[0][0].application_id == group.app.id
    assert say(group, "停止讨论", "stop")["status"] == "accepted"
    group.bridge.allowed = frozenset()
    assert group.bridge.dispatch_notice(current.id)
    assert len(group.sent) == 1


def test_group_export_retains_scope_but_restore_cannot_reactivate(group, tmp_path):
    current = adopt(group)
    drain(group, current.id)
    package = group.history.export(current.id, group.owner.id)
    assert package["payload"]["conversation"]["source_scope"] == "group_only"
    with group.store.transaction() as db:
        db.execute("DELETE FROM mentor_records WHERE kind='conversation' AND id=?", (current.id,))
        db.execute("DELETE FROM mentor_records WHERE owner=?", (current.id,))
    assert group.history.restore(package, group.owner.id, apply=True)["status"] == "stopped"
    restored = group.service.get(current.id, group.owner.id)
    assert restored.room_status == "sealed" and restored.room_id == ""
    with pytest.raises(MentorError):
        group.service.apply(Command(id="restore-run", principal_id=group.owner.id, conversation_id=restored.id,
            kind="start_run", expected_revision=restored.input_revision))


def replacement(group):
    with group.store.transaction() as db:
        group.store.bind(db, "room", key(group.app.platform, group.app.tenant, "verified-room"), group.conversation.id)
    return group.client.post("/api/mentors/v1/host-groups/adoptions",
        json={"expected_chat_id": "verified-room", "replace_conversation_id": group.conversation.id}, headers=group.owner_headers)


def test_existing_group_replacement_archives_old_personal_context_then_starts_clean(group):
    old_artifacts = group.store.list("artifact", group.conversation.id, Artifact)
    assert replacement(group).status_code == 200
    old = group.service.get(group.conversation.id, group.owner.id)
    assert old.status == "archived" and old.source_scope == "personal"
    assert group.store.list("artifact", old.id, Artifact) == old_artifacts
    assert not group.store.read("grant", old.grant_id, AudienceGrant).active
    assert say(group, "新问题：我打算学习绘画，还没开始", "new-topic")["status"] == "accepted"
    with group.store.transaction() as db:
        identifier = group.store.lookup(db, "room", key(group.app.platform, group.app.tenant, "verified-room"))
    assert identifier != old.id
    current = drain(group, identifier)
    assert current.source_scope == "group_only" and current.source_ids == ()
    assert "Synthetic problem" not in group.generator.calls[-1].model_dump_json()
    assert group.service.get(old.id, group.owner.id).status == "archived"


@pytest.mark.parametrize("status", ["sending", "unknown", "failed"])
def test_existing_group_unknown_outbound_blocks_migration(group, status):
    from riji_agent.mentors.models import Delivery
    delivery = Delivery(conversation_id=group.conversation.id, sequence=1, application_id=group.app.id,
        chat_id="verified-room", artifact_id="old-artifact", input_revision=1, cancel_epoch=0, status=status)
    with group.store.transaction() as db:
        group.store.put(db, "delivery", delivery, group.conversation.id)
    response = replacement(group)
    assert response.status_code == 409 and response.json()["detail"] == "delivery_reconciliation_required"
    assert group.service.get(group.conversation.id, group.owner.id).status == "completed"


def test_migration_cancels_pending_output_and_inflight_model_epoch(group):
    from riji_agent.mentors.models import Delivery, Execution
    old = group.conversation.model_copy(update={"status": "running", "lease_until": 1200, "lease_generation": 1})
    delivery = Delivery(conversation_id=old.id, sequence=1, application_id=group.app.id,
        chat_id="verified-room", artifact_id="old", input_revision=1, cancel_epoch=0)
    with group.store.transaction() as db:
        group.store.put(db, "conversation", old, old.owner_id)
        group.store.put(db, "delivery", delivery, old.id)
    assert replacement(group).status_code == 200
    assert group.store.read("delivery", delivery.id, Delivery).status == "cancelled"
    execution = Execution(conversation_id=old.id, run_id=old.run_id, input_revision=old.input_revision,
        cancel_epoch=0, lease_generation=1)
    with pytest.raises(MentorError, match="execution_inactive"):
        group.service.policy.check(execution)


def test_group_active_ordinary_input_gets_one_host_reply_then_explicit_resume(group):
    current = adopt(group)
    drain(group, current.id)
    assert say(group, "请四位分别给我参考", "start")["status"] == "accepted"
    original = group.service.get(current.id, group.owner.id)
    assert say(group, "补充：我更关心能否坚持", "interject")["status"] == "accepted"
    interrupted = drain(group, current.id)
    assert interrupted.suspended_run_id == original.run_id
    assert group.generator.calls[-1].actor == "host" and group.generator.calls[-1].stage == "followup"
    assert not any(call.stage == "opinion" for call in group.generator.calls)
    assert say(group, "/继续 " + current.id, "resume")["status"] == "accepted"
    resumed = drain(group, current.id)
    assert resumed.run_id == original.run_id and resumed.status == "completed"
