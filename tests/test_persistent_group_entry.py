"""Owned group input and natural controls, using synthetic channels only."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.mentors.api import build_router
from riji_agent.mentors.group_dialogue import GroupDialogue, parse_group_intent
from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.ingress import DiscussionIngress
from riji_agent.mentors.models import Account, Artifact, Envelope, MentorError
from test_mentor_discussions import drain, prepare, system  # noqa: F401


@pytest.mark.parametrize("text,kind", [
    ("上周试过了，有一点进展。", "supplement"),
    ("我准备每天练习", "supplement"),
    ("不要请大家辩论一下", "supplement"),
    ("请大家不要辩论", "supplement"),
    ("我不想让大家辩论", "supplement"),
    ("他昨天说‘请大家辩论一下’", "supplement"),
    ("“请大家辩论一下”", "supplement"),
    ("`请大家辩论一下`", "supplement"),
    ("> 请大家辩论一下", "supplement"),
    ("不要把这次结果记到日记里", "supplement"),
    ("不是停止讨论", "supplement"),
    ("如果我说停止讨论会怎样", "supplement"),
    ("“停止讨论”", "supplement"),
    ("收到！", "ack"),
    ("有道理", "ack"),
    ("停止讨论。", "stop"),
    ("继续上次", "continue"),
    ("重新分析", "reanalyze"),
    ("把这次结果记到日记里", "save"),
    ("归档这个问题", "archive"),
    ("恢复这个问题", "restore"),
    ("更正：现在只有两小时", "clarify"),
])
def test_control_intent_requires_direct_unquoted_nonnegated_request(text, kind):
    assert parse_group_intent(text).kind == kind


@pytest.mark.parametrize("text,mode,fresh,reanalyze,personas", [
    ("请几位导师分别给我参考。", "reference", False, False, ()),
    ("请大家就这个分歧辩论一下", "debate", False, False, ()),
    ("请四位重新讨论", "debate", True, False, ("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")),
    ("请大家重新参考", "reference", True, False, ()),
    ("请大家辩论一下，不沿用旧结论", "debate", True, True, ()),
    ("请温柔回顾者和直率教练分别给我参考", "reference", False, False,
     ("gentle_reviewer", "blunt_coach")),
    ("请温柔回顾者和直率教练重新讨论", "debate", True, False,
     ("gentle_reviewer", "blunt_coach")),
    ("请温柔回顾者和直率教练辩论，不沿用上次结论", "debate", True, True,
     ("gentle_reviewer", "blunt_coach")),
])
def test_explicit_table_selection_preserves_restart_and_reanalysis_distinction(text, mode, fresh, reanalyze, personas):
    intent = parse_group_intent(text)
    assert (intent.kind, intent.mode, intent.fresh_run, intent.reanalyze, intent.personas) == (
        "start_run", mode, fresh, reanalyze, personas)


@pytest.mark.parametrize("text,names,kind,actor", [
    ("直率教练，你为什么建议这样做？", (), "followup", "blunt_coach"),
    ("王阳明导师，请解释知行关系", (), "followup", "wang_yangming"),
    ("日记导师·温柔回顾者：还有其他看法吗", (), "followup", "gentle_reviewer"),
    ("请解释原因", ("日记导师·直率教练",), "followup", "blunt_coach"),
    ("直率教练，为什么？", ("日记导师·直率教练",), "followup", "blunt_coach"),
    ("直率教练，为什么？", ("日记导师·温柔回顾者",), "clarify", "host"),
    ("直率教练，温柔回顾者，你们怎么看？", (), "clarify", "host"),
    ("请解释原因", ("未知导师",), "clarify", "host"),
    ("请解释原因", ("",), "clarify", "host"),
    ("请解释原因", ("直率教练", "温柔回顾者"), "clarify", "host"),
    ("请大家辩论一下", ("直率教练",), "clarify", "host"),
    ("请解释原因", ("日记导师",), "supplement", "host"),
    ("请解释原因", (" 日记导师 ",), "supplement", "host"),
    ("收到", ("直率教练",), "ack", "host"),
])
def test_named_questions_have_one_unambiguous_actor(text, names, kind, actor):
    intent = parse_group_intent(text, names)
    assert (intent.kind, intent.actor) == (kind, actor)


@pytest.fixture
def entry(system):
    conversation = drain(system, prepare(system, "reference").id)
    service, _, _, _, _, principal, apps, _ = system
    worker = SimpleNamespace(wake=lambda: None)
    history = DiscussionHistory(service)
    ingress = DiscussionIngress(service, worker, history)
    def message(text, identifier="group-message", **changes):
        return Envelope(delivery_id="event-" + identifier, message_id=identifier,
            external_user_id="open-host", subject=principal.account.subject,
            external_chat_id=conversation.room_id, chat_type="group", text=text, **changes)
    return SimpleNamespace(service=service, conversation=conversation, principal=principal,
                           apps=apps, ingress=ingress, message=message, worker=worker, history=history)


def test_plain_group_feedback_is_one_host_response_without_new_table(system, entry):
    before = len(system[4].calls)
    entry.ingress.receive(entry.apps["host"].id, entry.message("上周试过了，有一点进展"))
    current = drain(system, entry.conversation.id)
    assert len(system[4].calls) == before + 1 and system[4].calls[-1].actor == "host"
    assert current.run_number == 1 and len(system[3].created) == 1


@pytest.mark.parametrize("names", [("未知导师",), ("温柔回顾者", "直率教练")])
def test_ambiguous_mentions_only_clarify_without_model_or_state_change(system, entry, names):
    before = len(system[4].calls)
    result = entry.ingress.receive(entry.apps["host"].id, entry.message("请解释原因", mentioned_names=names))
    assert "明确指定" in result["text"]
    current = drain(system, entry.conversation.id)
    assert len(system[4].calls) == before and current == entry.conversation


def test_mentor_question_is_exactly_one_reply_and_no_other_mentor_chain(system, entry):
    before = len(system[4].calls)
    entry.ingress.receive(entry.apps["host"].id, entry.message("直率教练，你为什么建议这样做？"))
    current = drain(system, entry.conversation.id)
    assert len(system[4].calls) == before + 1
    assert system[4].calls[-1].actor == "blunt_coach" and current.run_number == 1
    with pytest.raises(MentorError, match="sender_not_allowed"):
        entry.ingress.receive(entry.apps["host"].id,
            entry.message("请大家辩论一下", "bot-copy", sender_kind="bot"))


def test_group_event_duplicate_keeps_one_user_artifact_and_one_generation(system, entry):
    message = entry.message("上周试过了，有一点进展")
    first = entry.ingress.receive(entry.apps["host"].id, message)
    second = entry.ingress.receive(entry.apps["host"].id, message.model_copy(update={"delivery_id": "redelivered"}))
    assert first["conversation_id"] == second["conversation_id"] and second["status"] == "duplicate"
    drain(system, entry.conversation.id)
    artifacts = entry.service.store.list("artifact", entry.conversation.id, Artifact)
    assert sum(item.text == message.text and item.kind == "user" for item in artifacts) == 1
    with pytest.raises(MentorError, match="event_conflict"):
        entry.ingress.receive(entry.apps["host"].id, message.model_copy(update={"text": "请大家辩论一下"}))


def test_reference_upgrade_keeps_same_run_and_budget_but_reassessment_starts_new(system, entry):
    first = entry.conversation
    upgraded = entry.ingress.receive(entry.apps["host"].id, entry.message("请大家辩论一下", "upgrade"))
    assert "剩余额度" in upgraded["text"]
    current = drain(system, first.id)
    assert current.run_id == first.run_id and current.run_number == 1
    assert len(entry.service.budgets.status(current)["budgets"]) == 1
    restarted = entry.ingress.receive(entry.apps["host"].id, entry.message("请大家重新辩论", "new-run"))
    assert "开启一次" in restarted["text"]
    current = drain(system, first.id)
    assert current.run_id != first.run_id and current.run_number == 2
    assert len(entry.service.budgets.status(current)["budgets"]) == 2
    assert len(system[3].created) == 1


@pytest.mark.parametrize("text", ["请大家重新辩论", "请大家辩论，不沿用旧结论"])
def test_explicit_fresh_request_never_reuses_completed_reference_budget(system, entry, text):
    first = entry.conversation
    entry.ingress.receive(entry.apps["host"].id, entry.message(text))
    current = drain(system, first.id)
    assert current.run_id != first.run_id and current.run_number == 2
    assert current.reanalyze == ("不沿用" in text)


@pytest.mark.parametrize("change", ["owner", "room", "application"])
def test_stop_does_not_bypass_owner_managed_room_or_host_checks(system, entry, change):
    message = entry.message("停止讨论")
    application = entry.apps["host"]
    if change == "owner":
        message = message.model_copy(update={"subject": "unverified-stranger", "external_user_id": "stranger"})
    elif change == "room":
        message = message.model_copy(update={"external_chat_id": "unmanaged-room"})
    else:
        application = entry.apps["blunt_coach"]
    with pytest.raises(MentorError):
        entry.ingress.receive(application.id, message)
    assert entry.service.get(entry.conversation.id, entry.principal.id).status == "completed"


def test_stop_allowed_for_owner_when_managed_group_cannot_be_queried(system, entry):
    system[3].snapshot = None
    result = entry.ingress.receive(entry.apps["host"].id, entry.message("停止讨论"))
    assert "已停止" in result["text"]
    assert entry.service.get(entry.conversation.id, entry.principal.id).status == "stopped"


def test_verified_other_owner_still_cannot_stop_someone_elses_problem(system, entry):
    other = entry.service.identity.register_principal(
        Account(platform="test", tenant="tenant", subject="another-owner"), "other-legacy-owner")
    with pytest.raises(MentorError):
        entry.ingress.receive_owner(other.id, entry.conversation.id, "stop-attempt", "停止讨论")
    assert entry.service.get(entry.conversation.id, entry.principal.id).status == "completed"


def local_client(entry):
    runtime = SimpleNamespace(store=entry.service.store, user_tokens={"synthetic-owner-token": entry.principal.id},
        transport_tokens={"synthetic-transport-token": entry.apps["host"].id}, ingress=entry.ingress,
        service=entry.service, history=entry.history, worker=entry.worker, identity=entry.service.identity)
    app = FastAPI()
    app.include_router(build_router(runtime))
    return TestClient(app)


def test_local_http_message_requires_owner_token_and_is_idempotent_after_revision_changes(system, entry):
    with local_client(entry) as client:
        path = "/api/mentors/v1/conversations/" + entry.conversation.id + "/messages"
        body = {"id": "owner-message", "text": "上周有了一点进展"}
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers={"Authorization": "Bearer synthetic-transport-token"}).status_code == 401
        headers = {"Authorization": "Bearer synthetic-owner-token"}
        first = client.post(path, json=body, headers=headers)
        assert first.status_code == 200, first.text
        drain(system, entry.conversation.id)
        second = client.post(path, json=body, headers=headers)
        assert second.status_code == 200 and second.json()["deduplicated"]
        conflict = client.post(path, json={**body, "text": "请大家辩论一下"}, headers=headers)
        assert conflict.status_code == 409 and conflict.json()["detail"] == "event_conflict"
        wrong = client.post(path.replace(entry.conversation.id, "missing"), json=body, headers=headers)
        assert wrong.status_code == 409


def test_owner_message_receipt_survives_ingress_recreation(system, entry):
    identifier = entry.conversation.id
    first = entry.ingress.receive_owner(entry.principal.id, identifier, "message", "请大家辩论一下")
    drain(system, identifier)
    restarted = DiscussionIngress(entry.service, entry.worker, entry.history)
    second = restarted.receive_owner(entry.principal.id, identifier, "message", "请大家辩论一下")
    assert second == {**first, "deduplicated": True}
    assert entry.service.get(identifier, entry.principal.id).run_number == 1


def test_interrupted_owner_message_requires_reconciliation_instead_of_reexecution(entry):
    original = entry.ingress.groups.receive
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("Synthetic interrupted response")
    entry.ingress.groups.receive = interrupted
    with pytest.raises(TimeoutError):
        entry.ingress.receive_owner(entry.principal.id, entry.conversation.id, "interrupted", "上周有进展")
    current = entry.service.get(entry.conversation.id, entry.principal.id)
    restarted = DiscussionIngress(entry.service, entry.worker, entry.history)
    with pytest.raises(MentorError, match="incoming_reconciliation_required"):
        restarted.receive_owner(entry.principal.id, current.id, "interrupted", "上周有进展")
    assert entry.service.get(current.id, entry.principal.id) == current


def test_save_chooses_latest_available_unsuperseded_result(entry):
    selected = []
    artifacts = [{"id": "current", "kind": "comparison"},
                 {"id": "old", "kind": "synthesis", "superseded": True},
                 {"id": "withdrawn", "kind": "followup", "unavailable": "source_revoked"}]
    history = SimpleNamespace(read=lambda *args: {"artifacts": artifacts})
    def create(identifier, owner_id, artifact_ids, *, operation_id):
        selected.append((artifact_ids, operation_id))
        return SimpleNamespace(id="synthetic-handoff")
    groups = GroupDialogue(entry.service, history, SimpleNamespace(create=create))
    result = groups.save(entry.conversation, "save-operation")
    assert selected == [(("current",), "save-operation")]
    assert result["handoff_id"] == "synthetic-handoff"
    artifacts.pop(0)
    with pytest.raises(MentorError, match="discussion_result_unavailable"):
        groups.save(entry.conversation, "save-no-result")
    assert len(selected) == 1
