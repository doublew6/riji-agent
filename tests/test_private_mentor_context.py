"""Private continuity with synthetic model transport and actual domain guards."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.models import (
    Account, Artifact, Command, Delivery, Envelope, MentorError, Source,
)
from riji_agent.mentors.private_context import (
    MAX_PRIVATE_HISTORY_CHARS, MAX_PRIVATE_HISTORY_ITEMS, private_history,
)
from riji_agent.models.types import AssistantTurn
from test_mentor_discussions import drain, prepare, system  # noqa: F401

PERSONAS = ("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")


class WireProvider:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, Any]]] = []
        self.before_guard = lambda: None

    def complete_with_guard(self, messages, tools, *, before_send):
        self.before_guard()
        before_send()
        return self.complete(messages, tools)

    def complete(self, messages, tools):
        assert tools == []
        self.messages.append(deepcopy(messages))
        payload = json.loads(messages[-1]["content"])
        return AssistantTurn(content=json.dumps({
            "text": f"Synthetic reply {len(self.messages)}; advice is not a user fact.",
            "source_refs": [source["id"] for source in payload["background"]],
        }))


class Sources:
    def __init__(self, principal, persona):
        self.enabled = True
        self.source = Source(id="synthetic-source", owner_id=principal.id,
            version="v1", kind="memory", allowed_personas=(persona,), text="Synthetic allowed fact.")

    def background(self, principal, conversation):
        return (self.source,)

    def validate(self, principal, source):
        return self.enabled and source == self.source and source.owner_id == principal.id


def create_private(system, persona, question="Original synthetic condition.", **changes):
    service, _, _, _, _, principal, apps, _ = system
    message = Envelope(delivery_id="event", message_id="message", external_user_id="open-" + persona,
        subject=principal.account.subject, external_chat_id="private-" + persona,
        chat_type="p2p", text=question).model_copy(update=changes)
    binding = service.identity.resolve(apps[persona].id, message)[2]
    return service.create(binding, question, personas=(persona,))


def followup(system, current, text):
    service = system[0]
    current = service.get(current.id, current.owner_id)
    command = Command(id="followup-" + str(current.state_revision), principal_id=current.owner_id,
        conversation_id=current.id, kind="followup", expected_revision=current.input_revision, text=text)
    return service.apply(command), command


def use_wire(system):
    provider = WireProvider()
    system[1].generator = ModelGeneration(provider, system[0].identity.personas)
    return provider


def last_payload(provider):
    return json.loads(provider.messages[-1][-1]["content"])


@pytest.mark.parametrize("persona", PERSONAS)
def test_private_second_and_third_turn_keep_delivered_context_and_budget(system, persona):
    provider = use_wire(system)
    current = create_private(system, persona)
    drain(system, current.id)
    receipt, command = followup(system, current, "Remember the original condition before advising.")
    assert not receipt.deduplicated
    assert system[0].apply(command).deduplicated
    drain(system, current.id)
    second = last_payload(provider)
    assert [item["actor"] for item in second["previous"]] == ["user", persona, "user"]
    assert second["previous"][0]["text"] == current.question
    assert second["previous"][1]["text"].startswith("Synthetic reply 1")
    followup(system, current, "Compare this with both earlier replies.")
    final = drain(system, current.id)
    third = last_payload(provider)
    assert [item["actor"] for item in third["previous"]] == ["user", persona, "user", persona, "user"]
    assert third["question"] == "Compare this with both earlier replies."
    assert len(provider.messages) == 3 and len(system[0].budgets.status(final)["budgets"]) == 3
    assert system[0].budgets.status(final)["total_requests"] == 3


@pytest.mark.parametrize("persona", PERSONAS)
def test_private_history_does_not_mix_users_personas_chats_or_questions(system, persona):
    provider = use_wire(system)
    original = create_private(system, persona, "Selected problem context.")
    drain(system, original.id)
    other_persona = next(actor for actor in PERSONAS if actor != persona)
    others = [create_private(system, other_persona, "Other mentor secret."),
              create_private(system, persona, "Other chat secret.", external_chat_id="other-chat"),
              create_private(system, persona, "Other question secret.")]
    system[0].identity.register_principal(Account(platform="test", tenant="tenant", subject="other-owner"), "other")
    others.append(create_private(system, persona, "Other user secret.", subject="other-owner",
        external_user_id="other-open", external_chat_id="other-user-chat"))
    for item in others:
        # The helper drain assumes the fixture owner, so process these directly.
        for _ in range(5):
            system[1].run_one(item.id)
            system[2].dispatch_one(item.id)
    followup(system, original, "Continue the selected problem.")
    drain(system, original.id)
    encoded = json.dumps(last_payload(provider))
    assert "Selected problem context." in encoded and "Other " not in encoded
    assert [item["actor"] for item in last_payload(provider)["previous"]] == ["user", persona, "user"]


@pytest.mark.parametrize("persona", PERSONAS)
def test_private_context_preserves_source_and_ai_provenance(system, persona):
    sources = Sources(system[5], persona)
    system[0].policy.sources = sources
    provider = use_wire(system)
    current = create_private(system, persona)
    drain(system, current.id)
    prior = system[0].store.list("artifact", current.id, Artifact)[-1]
    followup(system, current, "I have not tried your suggestion yet.")
    drain(system, current.id)
    visible = next(item for item in last_payload(provider)["previous"] if item["id"] == prior.id)
    assert visible == prior.model_dump(mode="json")
    assert visible["origin_kind"] == "ai_discussion"
    assert visible["source_refs"] == [sources.source.id]
    assert visible["dependencies"] == list(current.source_ids)


@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("change", ["revoked", "version"])
def test_revoked_or_changed_sources_block_private_followup_before_model(system, persona, change):
    sources = Sources(system[5], persona)
    system[0].policy.sources = sources
    provider = use_wire(system)
    current = create_private(system, persona)
    drain(system, current.id)
    if change == "revoked":
        sources.enabled = False
    else:
        sources.source = sources.source.model_copy(update={"version": "v2", "text": "Changed synthetic fact."})
    followup(system, current, "Repeat your previous source-based answer.")
    final = drain(system, current.id)
    assert final.status == "interrupted" and len(provider.messages) == 1


@pytest.mark.parametrize("persona", PERSONAS)
def test_private_source_revocation_after_assembly_is_checked_at_send(system, persona):
    sources = Sources(system[5], persona)
    system[0].policy.sources = sources
    provider = use_wire(system)
    current = create_private(system, persona)
    drain(system, current.id)
    provider.before_guard = lambda: setattr(sources, "enabled", False)
    followup(system, current, "Continue after context assembly.")
    final = drain(system, current.id)
    assert final.status == "interrupted" and len(provider.messages) == 1


@pytest.mark.parametrize("status", ["pending", "sending", "failed", "unknown", "cancelled"])
def test_undelivered_private_replies_are_not_history(system, status):
    current = create_private(system, PERSONAS[0])
    drain(system, current.id)
    delivery = system[0].store.list("delivery", current.id, Delivery)[0]
    with system[0].store.transaction() as db:
        system[0].store.put(db, "delivery", delivery.model_copy(update={"status": status}), current.id)
    artifacts = system[0].store.list("artifact", current.id, Artifact)
    visible = private_history(system[0], current, artifacts)
    assert [item.kind for item in visible] == ["user"]


@pytest.mark.parametrize("persona", PERSONAS)
def test_stopped_before_delivery_does_not_create_phantom_shared_context(system, persona):
    provider = use_wire(system)
    current = create_private(system, persona)
    system[1].run_one(current.id)
    system[0].apply(Command(id="stop-before-delivery", principal_id=current.owner_id,
        conversation_id=current.id, kind="stop", expected_revision=0))
    followup(system, current, "Continue without assuming I saw an earlier reply.")
    drain(system, current.id)
    assert all(item["kind"] == "user" for item in last_payload(provider)["previous"])
    assert len(provider.messages) == 2


def synthetic_user(current, number, text="Synthetic user statement."):
    return Artifact(conversation_id=current.id, actor="user", kind="user",
        input_revision=1, run_id="old-run", origin_kind="user_statement",
        text=f"{number}: " + text, created_at=float(number))


@pytest.mark.parametrize("persona", PERSONAS)
def test_history_count_and_serialized_character_bounds_keep_latest_whole_records(system, persona):
    current = create_private(system, persona)
    many = tuple(synthetic_user(current, number) for number in range(40))
    selected = private_history(system[0], current, many)
    assert selected == many[-MAX_PRIVATE_HISTORY_ITEMS:]
    long = tuple(synthetic_user(current, number, "x" * 8000) for number in range(4))
    selected = private_history(system[0], current, long)
    encoded = json.dumps([item.model_dump(mode="json") for item in selected], ensure_ascii=False)
    assert len(encoded) <= MAX_PRIVATE_HISTORY_CHARS and 0 < len(selected) < len(long)
    assert selected == long[-len(selected):]
    assert all(len(item.text) > 8000 for item in selected)


def test_history_exact_json_boundary_and_oversized_metadata_are_bounded(system, monkeypatch):
    import riji_agent.mentors.private_context as module
    current = create_private(system, PERSONAS[0])
    records = (synthetic_user(current, 1), synthetic_user(current, 2))
    size = len(json.dumps([item.model_dump(mode="json") for item in records], ensure_ascii=False))
    monkeypatch.setattr(module, "MAX_PRIVATE_HISTORY_CHARS", size)
    assert private_history(system[0], current, records) == records
    monkeypatch.setattr(module, "MAX_PRIVATE_HISTORY_CHARS", size - 1)
    assert private_history(system[0], current, records) == records[-1:]
    huge = records[-1].model_copy(update={"claims": ("Synthetic metadata " * 2000,)})
    with pytest.raises(MentorError, match="private_history_too_large"):
        private_history(system[0], current, (huge,))
    assert private_history(system[0], current, (huge, *records)) == records[-1:]


def test_private_history_rejects_out_of_scope_dependency_and_ignores_foreign_records(system):
    current = create_private(system, PERSONAS[0])
    own = synthetic_user(current, 1)
    foreign = own.model_copy(update={"conversation_id": "foreign", "text": "Foreign secret."})
    assert private_history(system[0], current, (foreign, own)) == (own,)
    with pytest.raises(MentorError, match="source_unavailable"):
        private_history(system[0], current, (own.model_copy(update={"dependencies": ("foreign-source",)}),))
    with system[0].store.transaction() as db:
        system[0].store.bind(db, "origin", current.id, "missing-private-binding")
    with pytest.raises(MentorError, match="private_history_scope_invalid"):
        private_history(system[0], current, (own,))


def test_private_fix_keeps_roundtable_independent_first_opinions(system):
    private = create_private(system, PERSONAS[0], "Private context must not be shared.")
    drain(system, private.id)
    current = prepare(system, "reference")
    drain(system, current.id)
    opinions = [request for request in system[4].calls if request.stage == "opinion"]
    assert len(opinions) == 2 and all(not request.previous for request in opinions)
    assert all("Private context" not in request.conversation.question for request in opinions)


@pytest.mark.parametrize("persona", PERSONAS)
def test_private_revocation_before_delivery_suppresses_context_derived_reply(system, persona):
    sources = Sources(system[5], persona)
    system[0].policy.sources = sources
    provider = use_wire(system)
    current = create_private(system, persona)
    drain(system, current.id)
    followup(system, current, "Use the original allowed source again.")
    system[1].run_one(current.id)
    assert len(provider.messages) == 2 and len(system[3].sent) == 1
    sources.enabled = False
    system[2].dispatch_one(current.id)
    assert len(system[3].sent) == 1
    assert system[0].get(current.id, current.owner_id).status == "interrupted"


@pytest.mark.parametrize("changes", [{"chat_id": "another-private-chat"}, {"application_id": "another-app"}])
def test_delivery_to_another_window_is_not_visible_in_private_history(system, changes):
    current = create_private(system, PERSONAS[0])
    drain(system, current.id)
    delivery = system[0].store.list("delivery", current.id, Delivery)[0]
    with system[0].store.transaction() as db:
        system[0].store.put(db, "delivery", delivery.model_copy(update=changes), current.id)
    artifacts = system[0].store.list("artifact", current.id, Artifact)
    assert [item.kind for item in private_history(system[0], current, artifacts)] == ["user"]
