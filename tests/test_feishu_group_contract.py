"""Synthetic SDK contracts; these do not certify any real Feishu group."""

import json
from types import SimpleNamespace

import pytest

from riji_agent.mentors.feishu import FeishuChannel, normalize_host_group
from riji_agent.mentors.feishu_group import FeishuRoomInspector
from riji_agent.mentors.models import Account, Application, MentorError, Principal
from riji_agent.mentors.receiver import enqueue_event
from riji_agent.mentors.receiver_spool import ReceiverSpool

pytest.importorskip("lark_oapi")
from lark_oapi.api.im.v1 import (  # noqa: E402
    GetChatMembersResponse, GetChatMembersResponseBody, GetChatResponseBody, P2ImMessageReceiveV1,
)
from lark_oapi import JSON  # noqa: E402
from lark_oapi.core.model import RawResponse  # noqa: E402


def response(data):
    return SimpleNamespace(success=lambda: True, data=data)


def page(ids=("owner-open",), **changes):
    payload = {"items": [{"member_id_type": "open_id", "member_id": identifier,
                         "tenant_key": "test-tenant"} for identifier in ids],
               "has_more": False, "page_token": "", "member_total": 1,
               "trigger_security_conf_limit": False}
    return response(GetChatMembersResponseBody({**payload, **changes}))


def wire_page(ids=("owner-open",), **changes):
    """Use the SDK's actual RawResponse -> JSON -> response decoding path."""
    data = {"items": [{"member_id_type": "open_id", "member_id": identifier,
                       "tenant_key": "test-tenant"} for identifier in ids],
            "has_more": False, "member_total": 1, **changes}
    raw = RawResponse()
    raw.status_code = 200
    raw.content = json.dumps({"code": 0, "msg": "success", "data": data}).encode()
    parsed = JSON.unmarshal(raw.content.decode(), GetChatMembersResponse)
    parsed.raw = raw
    return parsed


@pytest.fixture
def group():
    apps = {name: Application(id=name, platform="feishu", tenant="test-tenant",
                             external_id="external-" + name, persona_id=name,
                             role="host" if name == "host" else "mentor")
            for name in ("host", "gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")}
    settings = {"chat_type": "private", "chat_mode": "group", "external": False,
                "chat_status": "normal", "moderation_permission": "all_members",
                "tenant_key": "test-tenant", "owner_id_type": "open_id", "owner_id": "owner-open",
                "user_count": "1", "bot_count": "5", "user_manager_id_list": [],
                "bot_manager_id_list": [], "add_member_permission": "only_owner",
                "share_card_permission": "not_allowed", "edit_permission": "only_owner",
                "membership_approval": "approval_required", "join_message_visibility": "only_owner",
                "restricted_mode_setting": {"status": True}}
    calls, pages = [], [page()]
    def chat(request):
        calls.append(("chat", request))
        return response(GetChatResponseBody(settings.copy()))
    def humans(request):
        calls.append(("humans", request))
        return pages.pop(0)
    def membership(app_id, request):
        calls.append((app_id, request))
        return response(SimpleNamespace(is_in_chat=True))
    clients = {name: SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        chat=SimpleNamespace(get=chat), chat_members=SimpleNamespace(
            get=humans, is_in_chat=lambda req, name=name: membership(name, req))))) for name in apps}
    return SimpleNamespace(apps=apps, clients=clients, settings=settings, calls=calls, pages=pages,
                           inspector=FeishuRoomInspector(apps, clients))


def test_full_observations_never_substitute_for_missing_history_or_continuity(group):
    result = group.inspector.inspect("test-room")
    assert result.human_open_ids == ("owner-open",)
    assert set(result.known_application_ids) == set(group.apps)
    assert result.human_pages_complete and result.known_bot_count_matches and result.settings_stable
    assert not result.snapshot.complete and not result.snapshot.history_restricted
    assert not result.snapshot.continuity_verified and not result.snapshot.management_restricted
    assert "feishu_history_visibility_unverified" in result.blockers
    assert "feishu_event_continuity_unverified" in result.blockers
    assert "feishu_host_group_input_unverified" in result.blockers
    # open_id is scoped to the host app; it cannot become a canonical principal.
    assert result.snapshot.human_subjects == ()
    assert [kind for kind, _ in group.calls].count("chat") == 2
    assert len(group.calls) == 8
    assert all(request.chat_id == "test-room" for _, request in group.calls)


def test_all_human_pages_are_read_and_expected_bots_check_their_own_token(group):
    group.settings["user_count"] = "2"
    group.pages[:] = [page(("owner-open",), has_more=True, page_token="next", member_total=2),
                      page(("other-open",), member_total=2)]
    result = group.inspector.inspect("test-room")
    assert result.human_open_ids == ("other-open", "owner-open") and result.human_pages_complete
    requests = [request for kind, request in group.calls if kind == "humans"]
    assert [request.page_token for request in requests] == [None, "next"]
    assert all(request.member_id_type == "open_id" for request in requests)
    assert {name for name, _ in group.calls if name in group.apps} == set(group.apps)


@pytest.mark.parametrize("changes,code", [
    ({"trigger_security_conf_limit": True}, "visibility"),
    ({"member_total": None}, "visibility"),
    ({"member_total": 2}, "visibility"),
    ({"has_more": None}, "pagination"),
    ({"has_more": True, "page_token": ""}, "pagination"),
    ({"items": []}, "count_mismatch"),
    ({"items": None}, "pagination"),
    ({"items": [{"member_id_type": "user_id", "member_id": "owner", "tenant_key": "test-tenant"}]}, "set"),
    ({"items": [{"member_id_type": "open_id", "member_id": "owner", "tenant_key": "other"}]}, "set"),
])
def test_partial_or_ambiguous_member_results_remain_unverified(group, changes, code):
    group.pages[:] = [page(**changes)]
    result = group.inspector.inspect("test-room")
    assert not result.human_pages_complete and not result.snapshot.complete
    assert any(code in blocker for blocker in result.blockers)


def test_repeated_page_token_and_missing_unique_members_are_not_complete(group):
    group.settings["user_count"] = "2"
    group.pages[:] = [page(("first",), has_more=True, page_token="same", member_total=2),
                      page(("second",), has_more=True, page_token="same", member_total=2)]
    assert "feishu_member_pagination_unverified" in group.inspector.inspect("test-room").blockers
    group.pages[:] = [page(("first", "first"), member_total=2)]
    assert "feishu_member_count_mismatch" in group.inspector.inspect("test-room").blockers


@pytest.mark.parametrize("across_pages", [False, True])
def test_wire_duplicate_owner_counts_once_only_after_pagination_exhausts(group, across_pages):
    first = wire_page(("owner-open", "owner-open"),
                      **({"has_more": True, "page_token": "next"} if across_pages else {}))
    assert len(json.loads(first.raw.content)["data"]["items"]) == len(first.data.items) == 2
    group.pages[:] = [first] + ([wire_page(("owner-open", "owner-open"))] if across_pages else [])
    result = verified_inspector(group).inspect("test-room")
    assert result.human_open_ids == ("owner-open",) and result.human_pages_complete
    assert result.snapshot.complete and result.member_principal_ids == ("owner-id",)
    assert not result.snapshot.history_restricted and not result.snapshot.continuity_verified
    requests = [request for kind, request in group.calls if kind == "humans"]
    assert [request.page_token for request in requests] == ([None, "next"] if across_pages else [None])
    assert not group.pages


@pytest.mark.parametrize("across_pages", [False, True])
@pytest.mark.parametrize("changes", [
    {"tenant_key": "other-tenant"}, {"tenant_key": None},
    {"member_id_type": "user_id"}, {"member_id_type": None},
    {"member_id": ""}, {"member_id": None}, {"member_id": 42}, {"member_id": "x" * 301},
])
def test_wire_repeated_identity_still_validates_each_item_before_dedup(group, across_pages, changes):
    valid = {"member_id_type": "open_id", "member_id": "owner-open", "tenant_key": "test-tenant"}
    invalid = {**valid, **changes}
    group.pages[:] = ([wire_page(items=[valid], has_more=True, page_token="next"),
                      wire_page(items=[invalid])] if across_pages else
                     [wire_page(items=[valid, invalid])])
    result = verified_inspector(group).inspect("test-room")
    assert "feishu_member_set_unverified" in result.blockers
    assert not result.human_pages_complete and not result.snapshot.complete


@pytest.mark.parametrize("expected,ids", [(1, ("owner-open", "owner-open", "stranger")),
                                         (2, ("owner-open", "owner-open"))])
def test_wire_duplicate_rows_cannot_hide_extra_or_missing_unique_people(group, expected, ids):
    group.settings["user_count"] = str(expected)
    group.pages[:] = [wire_page(ids, member_total=expected)]
    result = verified_inspector(group).inspect("test-room")
    assert "feishu_member_count_mismatch" in result.blockers
    assert not result.human_pages_complete and not result.snapshot.complete


def test_wire_duplicate_owner_does_not_hide_repeated_page_token(group):
    group.pages[:] = [wire_page(("owner-open", "owner-open"), has_more=True, page_token="same"),
                      wire_page(("owner-open",), has_more=True, page_token="same")]
    result = verified_inspector(group).inspect("test-room")
    assert "feishu_member_pagination_unverified" in result.blockers
    assert not result.human_pages_complete and not result.snapshot.complete
    assert [kind for kind, _ in group.calls].count("humans") == 2


@pytest.mark.parametrize("changes,code", [
    ({"has_more": True}, "pagination_unverified"),
    ({"has_more": None}, "pagination_unverified"),
    ({"member_total": 2}, "visibility_unverified"),
    ({"trigger_security_conf_limit": True}, "visibility_unverified"),
])
def test_wire_duplicate_owner_cannot_replace_page_or_visibility_evidence(group, changes, code):
    group.pages[:] = [wire_page(("owner-open", "owner-open"), **changes)]
    result = verified_inspector(group).inspect("test-room")
    assert "feishu_member_" + code in result.blockers
    assert not result.human_pages_complete and not result.snapshot.complete


def test_wire_duplicate_owner_never_completes_before_has_more_false(group):
    group.pages[:] = [wire_page(("owner-open", "owner-open"), has_more=True, page_token=str(index))
                      for index in range(21)]
    result = verified_inspector(group).inspect("test-room")
    assert "feishu_member_pagination_limit" in result.blockers
    assert not result.human_pages_complete and not result.snapshot.complete
    assert len(group.pages) == 1


def test_pagination_has_a_hard_read_bound(group):
    group.settings["user_count"] = "21"
    group.pages[:] = [page((str(index),), member_total=21, has_more=True, page_token=str(index))
                      for index in range(21)]
    result = group.inspector.inspect("test-room")
    assert "feishu_member_pagination_limit" in result.blockers and len(group.pages) == 1


@pytest.mark.parametrize("count", ["6", "4", "unknown", "-1", "１", None])
def test_extra_missing_or_unknown_bot_count_cannot_claim_a_complete_set(group, count):
    group.settings["bot_count"] = count
    result = group.inspector.inspect("test-room")
    assert not result.known_bot_count_matches and not result.snapshot.complete


@pytest.mark.parametrize("presence", [False, None, "true"])
def test_host_missing_or_ambiguous_membership_remains_blocked(group, presence):
    group.clients["host"].im.v1.chat_members.is_in_chat = lambda req: response(
        SimpleNamespace(is_in_chat=presence))
    result = group.inspector.inspect("test-room")
    assert not result.known_bot_count_matches
    assert any("membership_unverified" in code for code in result.blockers)


def test_sdk_failure_never_exposes_external_response_or_retries(group):
    attempts = []
    def fail(request):
        attempts.append(request)
        raise RuntimeError("Synthetic secret and private external response")
    group.clients["host"].im.v1.chat_members.get = fail
    result = group.inspector.inspect("test-room")
    assert result.human_open_ids == () and "feishu_room_check_failed" in result.blockers
    assert "secret" not in str(result) and len(attempts) == 1


def test_cross_tenant_or_ambiguous_host_fails_before_member_reads(group):
    group.settings["tenant_key"] = "other"
    result = group.inspector.inspect("test-room")
    assert "feishu_room_tenant_unverified" in result.blockers
    assert not result.room_attributes_verified and not result.snapshot.complete
    assert [kind for kind, _ in group.calls] == ["chat", "chat"]
    group.apps["other"] = group.apps["host"].model_copy(update={"id": "other"})
    with pytest.raises(MentorError, match="^host_application_required$"):
        group.inspector.inspect("test-room")


def verified_inspector(group, resolver=None):
    owner = Principal(id="owner-id", account=Account(
        platform="local", tenant="local-tenant", subject="canonical-owner"),
        legacy_owner_key="legacy-owner")
    if resolver is None:
        resolver = lambda app, external: owner if external == "owner-open" else None
    return FeishuRoomInspector(group.apps, group.clients, member_resolver=resolver)


def test_verified_identity_and_management_do_not_grant_history_or_continuity(group):
    calls = []
    owner = Principal(id="owner-id", account=Account(
        platform="local", tenant="local-tenant", subject="canonical-owner"),
        legacy_owner_key="legacy-owner")
    def resolve(app, external):
        calls.append((app, external))
        return owner
    result = verified_inspector(group, resolve).inspect("test-room")
    assert calls == [(group.apps["host"], "owner-open")]
    assert result.room_attributes_verified and result.member_mapping_verified
    assert result.member_principal_ids == ("owner-id",)
    assert result.owner_principal_id == "owner-id"
    assert result.snapshot.human_subjects == ("canonical-owner",)
    assert result.snapshot.complete and result.management_verified and result.speaking_allowed
    assert result.snapshot.management_restricted
    assert not result.snapshot.history_restricted and not result.snapshot.continuity_verified
    assert set(result.blockers) == {"feishu_history_visibility_unverified",
                                    "feishu_event_continuity_unverified",
                                    "feishu_host_group_input_unverified"}


@pytest.mark.parametrize("flag", ["missing", None, False])
def test_optional_security_field_needs_complete_pagination_and_independent_counts(group, flag):
    data = page().data
    if flag == "missing":
        del data.trigger_security_conf_limit
    else:
        data.trigger_security_conf_limit = flag
    group.pages[:] = [response(data)]
    assert verified_inspector(group).inspect("test-room").snapshot.complete


@pytest.mark.parametrize("flag", [True, 0, 1, "false", [], {}])
def test_explicit_security_limit_or_malformed_flag_cannot_pass(group, flag):
    data = page().data
    data.trigger_security_conf_limit = flag
    group.pages[:] = [response(data)]
    result = verified_inspector(group).inspect("test-room")
    assert "feishu_member_visibility_unverified" in result.blockers
    assert not result.human_pages_complete and not result.snapshot.complete


@pytest.mark.parametrize("changes,code", [
    ({"chat_status": "dissolved"}, "status"),
    ({"chat_status": None}, "status"),
    ({"chat_mode": "p2p"}, "mode"),
    ({"chat_mode": "topic"}, "mode"),
    ({"chat_type": "public"}, "not_private"),
    ({"external": True}, "external"),
    ({"external": None}, "external"),
])
def test_only_normal_same_tenant_private_groups_are_valid(group, changes, code):
    group.settings.update(changes)
    result = verified_inspector(group).inspect("test-room")
    assert not result.room_attributes_verified and not result.snapshot.private
    assert not result.snapshot.complete and not result.management_verified
    assert any(code in blocker for blocker in result.blockers)


def test_successful_partial_chat_response_is_not_an_empty_verified_room(group):
    group.settings.clear()
    group.settings.update(chat_status="normal", user_count="0", bot_count="0")
    result = verified_inspector(group).inspect("test-room")
    assert not result.snapshot.complete and not result.room_attributes_verified
    assert not result.human_pages_complete and not result.known_bot_count_matches
    assert "feishu_room_tenant_unverified" in result.blockers
    assert [kind for kind, _ in group.calls] == ["chat", "chat"]


@pytest.mark.parametrize("changes,code", [
    ({"add_member_permission": "all_members"}, "invitation_sharing"),
    ({"share_card_permission": "allowed"}, "invitation_sharing"),
    ({"edit_permission": "all_members"}, "edit"),
    ({"membership_approval": "no_approval_required"}, "approval"),
    ({"user_manager_id_list": ["unknown-human"]}, "user_managers"),
    ({"user_manager_id_list": None}, "user_managers"),
    ({"user_manager_id_list": ["owner-open", "owner-open"]}, "user_managers"),
    ({"bot_manager_id_list": ["unknown-app"]}, "bot_managers"),
    ({"bot_manager_id_list": None}, "bot_managers"),
    ({"bot_manager_id_list": ["external-host", "external-host"]}, "bot_managers"),
    ({"owner_id_type": "app_id", "owner_id": "external-host"}, "owner"),
    ({"owner_id": None, "owner_id_type": None}, "owner"),
    ({"owner_id": "unknown-human"}, "owner"),
    ({"owner_id": []}, "owner"),
])
def test_uncontrolled_management_is_reported_without_claiming_safe_settings(group, changes, code):
    group.settings.update(changes)
    result = verified_inspector(group).inspect("test-room")
    assert not result.management_verified and not result.snapshot.management_restricted
    assert any(code in blocker for blocker in result.blockers)


def test_already_controlled_bot_managers_are_recorded_without_changing_roles(group):
    group.settings["user_manager_id_list"] = ["owner-open"]
    group.settings["bot_manager_id_list"] = ["external-host", "external-blunt_coach"]
    result = verified_inspector(group).inspect("test-room")
    assert result.management_verified and result.speaking_allowed
    assert result.manager_application_ids == ("blunt_coach", "host")
    assert len(group.calls) == 8  # GetChat twice, humans once, self-membership five times.


@pytest.mark.parametrize("moderation", [None, "unknown", "moderator_list", "only_owner"])
def test_speaking_permission_is_not_inferred_from_membership(group, moderation):
    group.settings["moderation_permission"] = moderation
    result = verified_inspector(group).inspect("test-room")
    assert result.management_verified and not result.speaking_allowed
    assert "feishu_room_speaking_unverified" in result.blockers


def test_all_bots_must_be_managers_to_speak_under_owner_only_policy(group):
    group.settings["moderation_permission"] = "only_owner"
    group.settings["bot_manager_id_list"] = [app.external_id for app in group.apps.values()]
    result = verified_inspector(group).inspect("test-room")
    assert result.speaking_allowed and result.management_verified


@pytest.mark.parametrize("resolved", [None, "canonical-owner", {"id": "owner-id"}])
def test_unverified_resolver_values_do_not_become_canonical_identity(group, resolved):
    result = verified_inspector(group, lambda app, external: resolved).inspect("test-room")
    assert not result.member_mapping_verified and not result.snapshot.complete
    assert result.snapshot.human_subjects == () and not result.owner_principal_id
    assert "feishu_canonical_member_mapping_unverified" in result.blockers


def test_resolver_failure_is_safe_and_does_not_leak_private_details(group):
    def fail(app, external):
        raise RuntimeError("private resolver details")
    result = verified_inspector(group, fail).inspect("test-room")
    assert not result.member_mapping_verified and "private resolver" not in str(result)


def test_two_external_members_cannot_be_collapsed_into_one_owner(group):
    group.settings["user_count"] = "2"
    group.pages[:] = [page(("owner-open", "second-open"), member_total=2)]
    owner = Principal(id="owner-id", account=Account(
        platform="feishu", tenant="test-tenant", subject="owner-subject"),
        legacy_owner_key="legacy-owner")
    result = verified_inspector(group, lambda app, external: owner).inspect("test-room")
    assert not result.member_mapping_verified and not result.management_verified
    assert not result.snapshot.complete


def test_complete_mapping_of_an_extra_human_does_not_make_a_private_owner_room(group):
    group.settings["user_count"] = "2"
    group.pages[:] = [page(("owner-open", "second-open"), member_total=2)]
    def resolve(app, external):
        return Principal(id=external, account=Account(
            platform="feishu", tenant=app.tenant, subject="canonical-" + external),
            legacy_owner_key="legacy-" + external)
    result = verified_inspector(group, resolve).inspect("test-room")
    assert result.snapshot.complete and result.member_mapping_verified
    assert not result.owner_principal_id and not result.management_verified
    assert not result.snapshot.management_restricted


@pytest.mark.parametrize("status,expected", [
    (99991672, "feishu_room_permission_missing"),
    (232011, "feishu_room_membership_required"),
    (500, "feishu_room_check_failed"),
])
def test_api_denials_are_distinct_safe_evidence_codes(group, status, expected):
    group.clients["host"].im.v1.chat_members.get = lambda req: SimpleNamespace(
        success=lambda: False, code=status, data=None, msg="private response")
    result = verified_inspector(group).inspect("test-room")
    assert expected in result.blockers and "private response" not in str(result)
    assert not result.snapshot.complete


def test_settings_change_during_check_does_not_pass_or_reuse_configuration(group):
    before = group.settings.copy()
    def changed(request):
        group.settings["user_manager_id_list"] = ["unexpected-admin"]
        return response(SimpleNamespace(is_in_chat=True))
    group.clients["host"].im.v1.chat_members.is_in_chat = changed
    result = verified_inspector(group).inspect("test-room")
    assert not result.settings_stable and "feishu_room_changed_during_check" in result.blockers
    assert not result.snapshot.complete and not result.management_verified
    group.settings.clear()
    group.settings.update(before)
    group.pages[:] = [page()]
    group.clients["host"].im.v1.chat_members.is_in_chat = lambda req: response(SimpleNamespace(is_in_chat=True))
    clean = verified_inspector(group).inspect("test-room")
    assert clean.snapshot.complete and clean.management_verified
    assert clean.snapshot.configuration_version != result.snapshot.configuration_version


def test_channel_cannot_create_a_room_from_observed_restriction_fields(group):
    channel = FeishuChannel(group.apps, group.clients)
    assert not channel.inspect_room("test-room").complete
    with pytest.raises(MentorError, match="^feishu_roundtable_capability_not_verified$"):
        channel.create_room(None, tuple(group.apps), "synthetic-operation")


def test_channel_passes_verified_resolver_without_enabling_group_creation(group):
    inspector = verified_inspector(group)
    channel = FeishuChannel(group.apps, group.clients, member_resolver=inspector.member_resolver)
    result = channel.inspect_room_evidence("test-room")
    assert result.snapshot.complete and result.management_verified
    assert not result.snapshot.history_restricted and not result.snapshot.continuity_verified
    with pytest.raises(MentorError, match="^feishu_roundtable_capability_not_verified$"):
        channel.create_room(None, tuple(group.apps), "synthetic-operation")


def event(*, app="external-host", tenant="test-tenant", sender="user", chat_type="group", mentions=()):
    return P2ImMessageReceiveV1({"schema": "2.0", "header": {"event_id": "event", "app_id": app},
        "event": {"sender": {"sender_type": sender, "tenant_key": tenant,
            "sender_id": {"open_id": "owner-open"}}, "message": {"message_id": "message",
            "chat_id": "test-room", "chat_type": chat_type, "message_type": "text",
            "content": json.dumps({"text": "@_user_1 Please explain"}), "mentions": list(mentions)}}})


def test_host_group_normalization_preserves_bounded_untrusted_mention_hints(group):
    message = normalize_host_group(event(mentions=[{"key": "@_user_1", "id": {"open_id": "bot-open"},
        "name": "日记导师·直率教练"}]), group.apps["host"])
    assert message.chat_type == "group" and message.text == "Please explain"
    assert message.mentioned_external_ids == ("bot-open",)
    assert message.mentioned_names == ("日记导师·直率教练",)
    assert message.external_user_id == "owner-open" and message.subject == ""


@pytest.mark.parametrize("changes,code", [
    ({"app": "external-other"}, "identity_unverified"),
    ({"tenant": "other"}, "identity_unverified"),
    ({"sender": "app"}, "identity_unverified"),
    ({"chat_type": "p2p"}, "group_message_required"),
    ({"mentions": [{"key": "x", "name": "x" * 101}]}, "mentions_invalid"),
    ({"mentions": [{"key": "x", "name": "name"}] * 11}, "mentions_invalid"),
])
def test_group_edge_rejects_forged_identity_and_malformed_target(group, changes, code):
    with pytest.raises(MentorError, match=code):
        normalize_host_group(event(**changes), group.apps["host"])


def test_only_host_group_copies_queue_and_repeated_event_is_one_receipt(group, tmp_path):
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o700)
    spool = ReceiverSpool(directory / "spool.sqlite3", "external-host")
    worker = SimpleNamespace(spool=spool, wake=lambda: None)
    enqueue_event(event(), group.apps["host"], worker)
    enqueue_event(event(), group.apps["host"], worker)
    assert spool.status() == {"queued": 1}
    enqueue_event(event(app="external-gentle_reviewer"), group.apps["gentle_reviewer"], worker)
    enqueue_event(event(sender="app"), group.apps["host"], worker)
    assert spool.status() == {"queued": 1}
