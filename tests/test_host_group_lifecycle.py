"""Synthetic raw lifecycle events invalidate one group without reading its body."""
import pytest

from test_hermes_host_group_bridge import host
from test_group_only_roundtable import group, adopt, drain, say
from riji_agent.mentors.models import Conversation


def lifecycle(group, *, event_type="im.chat.member.user.added_v1", room="verified-room", event_id="event-1", **header):
    raw = {"schema": "2.0", "header": {"app_id": group.app.external_id, "tenant_key": group.app.tenant,
        "event_type": event_type, "event_id": event_id, "create_time": "1000000", **header},
        "event": {"chat_id": room, "operator_id": {"open_id": "arbitrary-operator"}}}
    return group.client.post("/api/mentors/v1/host-lifecycle", json={"raw_event": raw}, headers=group.headers)


@pytest.mark.parametrize("event_type", ["im.chat.member.user.added_v1", "im.chat.member.user.deleted_v1",
    "im.chat.member.user.withdrawn_v1", "im.chat.updated_v1", "im.chat.member.bot.deleted_v1"])
def test_lifecycle_pauses_exact_room_and_deduplicates_without_model(group, event_type):
    current = adopt(group)
    before_other = group.service.get(group.conversation.id, group.owner.id)
    response = lifecycle(group, event_type=event_type)
    assert response.status_code == 200 and response.json()["code"] == "group_verification_paused"
    paused = group.service.get(current.id, group.owner.id)
    assert paused.status == "interrupted" and paused.room_status == "verification_paused"
    assert lifecycle(group, event_type=event_type).json()["duplicate"] is True
    assert group.service.get(current.id, group.owner.id).cancel_epoch == paused.cancel_epoch
    assert group.service.get(group.conversation.id, group.owner.id) == before_other
    assert not group.generator.calls and not group.sent
    assert say(group, "停止讨论", "stop")["status"] == "accepted"


@pytest.mark.parametrize("changes", [{"tenant_key": "other"}, {"app_id": "other"},
    {"event_type": "im.message.receive_v1"}, {"create_time": "0"}, {"event_id": ""}])
def test_wrong_lifecycle_header_does_not_invalidate_group(group, changes):
    current = adopt(group)
    assert lifecycle(group, **changes).status_code == 409
    assert group.service.get(current.id, group.owner.id) == current


def test_lifecycle_unmanaged_chat_and_bad_secret_do_not_change_scope(group):
    current = adopt(group)
    assert lifecycle(group, room="other-chat").status_code == 409
    group.headers = {"X-Hermes-Secret": "wrong"}
    assert lifecycle(group).status_code == 401
    assert group.service.get(current.id, group.owner.id) == current


def test_revalidation_uses_real_current_proof_and_leaves_run_stopped(group):
    current = adopt(group)
    lifecycle(group)
    group.evidence.speaking_allowed = False
    url = f"/api/mentors/v1/host-groups/{current.id}/revalidate"
    assert group.client.post(url, headers=group.owner_headers).status_code == 409
    group.evidence.speaking_allowed = True
    response = group.client.post(url, headers=group.owner_headers)
    assert response.status_code == 200 and response.json()["status"] == "stopped"
    restored = group.service.get(current.id, group.owner.id)
    assert restored.room_status == "ready" and not group.generator.calls
    assert say(group, "我目前仍计划每周两小时", "after-revalidate")["status"] == "accepted"
    assert drain(group, current.id).status == "completed"


def test_full_lifecycle_receipt_capacity_still_pauses_the_verified_target(group):
    from riji_agent.mentors.host_lifecycle import LifecycleReceipt
    current = adopt(group)
    with group.store.transaction() as db:
        for number in range(2000):
            group.store.put(db, "host_lifecycle", LifecycleReceipt(id=str(number), fingerprint="test", received_at=1000))
    response = lifecycle(group)
    assert response.status_code == 200 and response.json()["receipt_recorded"] is False
    assert group.service.get(current.id, group.owner.id).room_status == "verification_paused"
    assert len(group.store.list("host_lifecycle", "", LifecycleReceipt)) == 2000
    assert not group.generator.calls and not group.sent


@pytest.mark.parametrize("state", ["stopped", "archived"])
def test_lifecycle_invalidates_room_without_unarchiving_or_restarting_owner_stop(group, state):
    from riji_agent.mentors.models import Command
    current = adopt(group)
    group.service.apply(Command(id="inactivate", principal_id=group.owner.id, conversation_id=current.id,
        kind="archive" if state == "archived" else "stop", expected_revision=current.input_revision))
    assert lifecycle(group).status_code == 200
    after = group.service.get(current.id, group.owner.id)
    assert after.status == state and after.room_status == "verification_paused"
    assert not group.model_worker.run_one(current.id)
