"""Current-audience proof for group-only material; no personal source grant."""
from __future__ import annotations

from riji_agent.mentors.models import Application, Artifact, Conversation, MentorError, Principal, WorkingSummary

GROUP_PERSONAS = ("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")


def current_snapshot(policy, conversation: Conversation):
    if conversation.source_scope != "group_only" or conversation.source_ids:
        raise MentorError("group_only_source_violation")
    owner = policy.store.read("principal", conversation.owner_id, Principal)
    authorize = getattr(policy, "group_owner_authorizer", None)
    if owner is None or authorize is None or authorize(owner.legacy_owner_key).id != owner.id:
        raise MentorError("group_owner_unverified")
    host = policy.store.read("application", conversation.source_application_id, Application)
    if (host is None or host.platform != "feishu" or host.role != "host" or host.persona_id != "host"
            or host.platform != conversation.source_platform or host.tenant != conversation.source_tenant
            or conversation.personas != GROUP_PERSONAS):
        raise MentorError("group_application_unverified")
    apps = policy.store.list("application", "", Application)
    selected = []
    for actor in ("host", *GROUP_PERSONAS):
        matches = [app for app in apps if app.platform == host.platform and app.tenant == host.tenant
                   and app.persona_id == actor and app.role == ("host" if actor == "host" else "mentor")]
        if len(matches) != 1:
            raise MentorError("group_application_unverified")
        selected.append(matches[0].id)
    if selected[0] != host.id:
        raise MentorError("group_application_unverified")
    adapter = policy.channel.adapters.get("feishu")
    if adapter is None:
        raise MentorError("group_room_unverified")
    evidence = adapter.inspect_room_evidence(conversation.room_id)
    snapshot = evidence.snapshot
    valid = (snapshot.room_id == conversation.room_id and snapshot.private and snapshot.complete
             and snapshot.management_restricted and evidence.management_verified and evidence.speaking_allowed
             and evidence.room_attributes_verified and evidence.member_mapping_verified
             and evidence.human_pages_complete and evidence.known_bot_count_matches and evidence.settings_stable
             and evidence.owner_principal_id == owner.id and tuple(evidence.member_principal_ids) == (owner.id,)
             and tuple(evidence.human_open_ids) == (owner.legacy_owner_key,)
             and set(snapshot.human_subjects) == {owner.account.subject}
             and set(snapshot.application_ids) == set(selected)
             and set(evidence.known_application_ids) == set(selected))
    if not valid:
        raise MentorError("group_room_unverified")
    # The Inspector's history/continuity values are kept verbatim. This grant
    # authorizes only data received in this room, never personal sources.
    return snapshot


def check_content(store, conversation: Conversation) -> None:
    """Reject alien premises before any source validation/retrieval can run."""
    if conversation.source_ids:
        raise MentorError("group_only_source_violation")
    artifacts = store.list("artifact", conversation.id, Artifact)
    ids = {item.id for item in artifacts}
    if any(item.conversation_id != conversation.id or item.origin_room_id != conversation.room_id
           or item.dependencies or not set(item.source_refs).issubset(ids) for item in artifacts):
        raise MentorError("group_only_source_violation")
    for summary in store.list("summary", conversation.id, WorkingSummary):
        if summary.conversation_id != conversation.id or any(
            item.dependencies or not set(item.source_refs).issubset(ids)
            or not set(item.artifact_ids).issubset(ids) for item in summary.items):
            raise MentorError("group_only_source_violation")
