"""Read-only group observations; platform observations never grant disclosure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from riji_agent.mentors.models import Application, MentorError, Principal, RoomSnapshot

MemberResolver = Callable[[Application, str], Principal | None]

_CHAT_FIELDS = (
    "chat_type", "chat_mode", "chat_status", "external", "tenant_key",
    "owner_id_type", "owner_id", "user_manager_id_list", "bot_manager_id_list",
    "user_count", "bot_count", "add_member_permission", "share_card_permission",
    "edit_permission", "membership_approval", "join_message_visibility",
    "leave_message_visibility", "group_message_type", "moderation_permission",
)
_RESTRICTION_FIELDS = (
    "status", "screenshot_has_permission_setting",
    "download_has_permission_setting", "message_has_permission_setting",
)
_UNPROVEN = (
    "feishu_history_visibility_unverified",
    "feishu_event_continuity_unverified",
    "feishu_host_group_input_unverified",
)


@dataclass(frozen=True)
class FeishuRoomEvidence:
    """Independent observations, including any verified local identity mappings."""

    snapshot: RoomSnapshot
    human_open_ids: tuple[str, ...]
    known_application_ids: tuple[str, ...]
    human_pages_complete: bool
    known_bot_count_matches: bool
    settings_stable: bool
    blockers: tuple[str, ...]
    room_attributes_verified: bool = False
    member_mapping_verified: bool = False
    member_principal_ids: tuple[str, ...] = ()
    owner_principal_id: str = ""
    management_verified: bool = False
    manager_application_ids: tuple[str, ...] = ()
    speaking_allowed: bool = False


@dataclass(frozen=True)
class _Members:
    humans: tuple[str, ...] = ()
    bots: tuple[str, ...] = ()
    humans_complete: bool = False
    bots_complete: bool = False


class FeishuRoomInspector:
    """Bounded SDK reads with no registration, send, configuration or retry."""

    def __init__(self, applications: Mapping[str, Application],
                 clients: Mapping[str, Any], *,
                 member_resolver: MemberResolver | None = None) -> None:
        self.applications, self.clients = applications, clients
        # The composition root supplies a read-only lookup of verified links.
        # Group content, display names and callers cannot enroll an identity.
        self.member_resolver = member_resolver

    def inspect(self, room_id: str) -> FeishuRoomEvidence:
        host = self._host()
        before = self._chat(host, room_id)
        blockers = list(_UNPROVEN)
        members = self._members(host, room_id, before, blockers)
        after = self._chat(host, room_id)
        stable = before == after
        if not stable:
            blockers.append("feishu_room_changed_during_check")
        room_valid = _room_attributes(after, host.tenant, blockers)
        principals = self._canonical_members(host, members.humans, blockers)
        mapped = members.humans_complete and bool(principals)
        owner = _owner(after, members.humans, principals, blockers)
        apps = {app.external_id: app.id for app in self.applications.values()
                if app.id in members.bots}
        managed, managers = _management(after, owner, apps, blockers)
        speaking = _speaking(after, apps, blockers)
        complete = room_valid and stable and mapped and members.bots_complete
        version = hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()
        snapshot = RoomSnapshot(
            room_id=room_id,
            human_subjects=tuple(principal.account.subject for principal in principals),
            application_ids=members.bots, private=room_valid, complete=complete,
            management_restricted=complete and managed,
            history_restricted=False, continuity_verified=False,
            configuration_version=version,
        )
        return FeishuRoomEvidence(
            snapshot=snapshot, human_open_ids=members.humans,
            known_application_ids=members.bots,
            human_pages_complete=members.humans_complete,
            known_bot_count_matches=members.bots_complete, settings_stable=stable,
            blockers=tuple(dict.fromkeys(blockers)), room_attributes_verified=room_valid,
            member_mapping_verified=mapped,
            member_principal_ids=tuple(principal.id for principal in principals),
            owner_principal_id=owner, management_verified=complete and managed,
            manager_application_ids=managers, speaking_allowed=complete and speaking,
        )

    def _members(self, host: Application, room_id: str,
                 settings: dict[str, Any], blockers: list[str]) -> _Members:
        if settings["tenant_key"] != host.tenant:
            blockers.append("feishu_room_tenant_unverified")
            return _Members()
        humans, human_complete = (), False
        bots, bot_count_matches = (), False
        try:
            humans = self._humans(host, room_id, _count(settings["user_count"]))
            human_complete = True
        except MentorError as exc:
            blockers.append(exc.code)
        try:
            bots = self._bots(host, room_id)
            bot_count_matches = len(bots) == _count(settings["bot_count"])
            if not bot_count_matches:
                blockers.append("feishu_bot_set_unverified")
        except MentorError as exc:
            blockers.append(exc.code)
        return _Members(humans, bots, human_complete, bot_count_matches)

    def _canonical_members(self, host: Application, humans: tuple[str, ...],
                           blockers: list[str]) -> tuple[Principal, ...]:
        try:
            if self.member_resolver is None or not humans:
                raise ValueError
            principals = tuple(self.member_resolver(host, identifier) for identifier in humans)
            if (any(not isinstance(principal, Principal) or not principal.id
                    or not principal.account.subject for principal in principals)
                    or len({principal.id for principal in principals}) != len(humans)):
                raise ValueError
            return principals
        except Exception:
            blockers.append("feishu_canonical_member_mapping_unverified")
            return ()

    def _host(self) -> Application:
        hosts = [app for app in self.applications.values()
                 if app.platform == "feishu" and app.role == "host"]
        if len(hosts) != 1 or hosts[0].id not in self.clients:
            raise MentorError("host_application_required")
        return hosts[0]

    def _chat(self, host: Application, room_id: str) -> dict[str, Any]:
        from lark_oapi.api.im.v1 import GetChatRequest
        request = GetChatRequest.builder().chat_id(room_id).user_id_type("open_id").build()
        data = _read(self.clients[host.id].im.v1.chat.get, request)
        fields = {name: getattr(data, name, None) for name in _CHAT_FIELDS}
        restricted = getattr(data, "restricted_mode_setting", None)
        fields["restricted_mode_setting"] = {
            name: getattr(restricted, name, None) for name in _RESTRICTION_FIELDS}
        return fields

    def _humans(self, host: Application, room_id: str, expected: int) -> tuple[str, ...]:
        from lark_oapi.api.im.v1 import GetChatMembersRequest
        members: set[str] = set()
        seen_tokens: set[str] = set()
        token = ""
        for _ in range(20):
            builder = GetChatMembersRequest.builder().chat_id(room_id)
            builder.member_id_type("open_id").page_size(100)
            if token:
                builder.page_token(token)
            data = _read(self.clients[host.id].im.v1.chat_members.get, builder.build())
            _validate_page(data, expected)
            for item in data.items:
                identifier = getattr(item, "member_id", None)
                if (getattr(item, "member_id_type", None) != "open_id"
                        or not isinstance(identifier, str) or not identifier
                        or len(identifier) > 300
                        or getattr(item, "tenant_key", None) != host.tenant):
                    raise MentorError("feishu_member_set_unverified")
                # Feishu can repeat the same validated member within/across
                # pages. Count identities after validation; duplicate entries
                # neither add people nor replace exhausted, counted pagination.
                members.add(identifier)
            if len(members) > expected:
                raise MentorError("feishu_member_count_mismatch")
            if data.has_more is False:
                if len(members) != expected:
                    raise MentorError("feishu_member_count_mismatch")
                return tuple(sorted(members))
            token = getattr(data, "page_token", None)
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise MentorError("feishu_member_pagination_unverified")
            seen_tokens.add(token)
        raise MentorError("feishu_member_pagination_limit")

    def _bots(self, host: Application, room_id: str) -> tuple[str, ...]:
        from lark_oapi.api.im.v1 import IsInChatChatMembersRequest
        applications = [app for app in self.applications.values()
                        if app.platform == "feishu" and app.tenant == host.tenant]
        if (len({app.external_id for app in applications}) != len(applications)
                or len(applications) > 10):
            raise MentorError("feishu_application_set_unverified")
        present = []
        request = IsInChatChatMembersRequest.builder().chat_id(room_id).build()
        for app in applications:
            if app.id not in self.clients:
                raise MentorError("feishu_bot_membership_unverified")
            # This endpoint checks its own token's bot, not an arbitrary app_id.
            data = _read(self.clients[app.id].im.v1.chat_members.is_in_chat, request)
            if type(getattr(data, "is_in_chat", None)) is not bool:
                raise MentorError("feishu_bot_membership_unverified")
            if data.is_in_chat:
                present.append(app.id)
        if host.id not in present:
            raise MentorError("feishu_host_membership_unverified")
        return tuple(sorted(present))


def _validate_page(data: Any, expected: int) -> None:
    limited = getattr(data, "trigger_security_conf_limit", None)
    # Official member responses may omit this optional SDK field. Completeness
    # still requires exhausted pagination and counts from two independent reads.
    if (limited is not None and (type(limited) is not bool or limited)
            or type(getattr(data, "member_total", None)) is not int
            or data.member_total != expected):
        raise MentorError("feishu_member_visibility_unverified")
    if (type(getattr(data, "has_more", None)) is not bool
            or not isinstance(getattr(data, "items", None), list)
            or len(data.items) > 2000):
        raise MentorError("feishu_member_pagination_unverified")


def _room_attributes(settings: dict[str, Any], tenant: str, blockers: list[str]) -> bool:
    checks = (
        (settings["tenant_key"] == tenant, "feishu_room_tenant_unverified"),
        (settings["chat_status"] == "normal", "feishu_room_status_unverified"),
        (settings["chat_mode"] == "group", "feishu_room_mode_unverified"),
        (settings["chat_type"] == "private", "feishu_room_not_private"),
        (settings["external"] is False, "feishu_room_external_unverified"),
    )
    blockers.extend(code for passed, code in checks if not passed)
    return all(passed for passed, _ in checks)


def _owner(settings: dict[str, Any], humans: tuple[str, ...],
           principals: tuple[Principal, ...], blockers: list[str]) -> str:
    # GetChat does not expose a robot owner's ID. Missing fields cannot prove
    # that the host bot owns this room, even if its display name looks familiar.
    if (settings["owner_id_type"] != "open_id" or len(humans) != 1
            or settings["owner_id"] != humans[0] or len(principals) != 1):
        blockers.append("feishu_room_owner_unverified")
        return ""
    return principals[0].id


def _management(settings: dict[str, Any], owner: str,
                applications: dict[str, str], blockers: list[str]) -> tuple[bool, tuple[str, ...]]:
    humans = _identifiers(settings["user_manager_id_list"])
    bots = _identifiers(settings["bot_manager_id_list"])
    checks = (
        (bool(owner), "feishu_management_owner_unverified"),
        (humans is not None and isinstance(settings["owner_id"], str)
         and humans <= {settings["owner_id"]},
         "feishu_user_managers_unverified"),
        (bots is not None and bots <= applications.keys(), "feishu_bot_managers_unverified"),
        (settings["add_member_permission"] == "only_owner"
         and settings["share_card_permission"] == "not_allowed",
         "feishu_invitation_sharing_unrestricted"),
        (settings["edit_permission"] == "only_owner", "feishu_room_edit_unrestricted"),
        (settings["membership_approval"] == "approval_required",
         "feishu_membership_approval_unverified"),
    )
    blockers.extend(code for passed, code in checks if not passed)
    managers = tuple(sorted(applications[identifier] for identifier in bots or ()
                            if identifier in applications))
    return all(passed for passed, _ in checks), managers


def _speaking(settings: dict[str, Any], applications: dict[str, str],
              blockers: list[str]) -> bool:
    moderation = settings["moderation_permission"]
    managers = _identifiers(settings["bot_manager_id_list"])
    allowed = moderation == "all_members" or (
        moderation == "only_owner" and managers is not None
        and bool(applications) and applications.keys() <= managers)
    if not allowed:
        # moderator_list needs its own complete authenticated API inspection.
        blockers.append("feishu_room_speaking_unverified")
    return allowed


def _identifiers(value: Any) -> set[str] | None:
    if (not isinstance(value, list) or len(value) > 100
            or any(not isinstance(item, str) or not item or len(item) > 300 for item in value)
            or len(set(value)) != len(value)):
        return None
    return set(value)


def _count(value: Any) -> int:
    if (not isinstance(value, str) or not value.isascii() or not value.isdigit()
            or len(value) > 5 or int(value) > 10000):
        raise MentorError("feishu_member_count_unverified")
    return int(value)


def _read(method: Any, request: Any) -> Any:
    try:
        response = method(request)
        success, data, code = response.success(), response.data, getattr(response, "code", None)
    except Exception:
        raise MentorError("feishu_room_check_failed") from None
    if success is not True or data is None:
        errors = {99991672: "feishu_room_permission_missing",
                  232011: "feishu_room_membership_required"}
        error = (errors.get(code, "feishu_room_check_failed")
                 if type(code) is int else "feishu_room_check_failed")
        raise MentorError(error)
    return data
