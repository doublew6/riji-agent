"""Synthetic end-to-end contracts; these are not real-model quality scores."""

from dataclasses import replace

import pytest

from riji_agent.mentors.delivery import OutboxDispatcher
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.models import (
    Account, Application, Artifact, Command, Conversation, Delivery, Envelope,
    ComparisonFinding, ComparisonStance, Generation, MentorError, RoomSnapshot, Source, TransportResult,
)
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.ports import NoSources
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import MentorStore
from riji_agent.mentors.worker import DiscussionWorker
from riji_agent.personas.registry import PersonaRegistry


class SyntheticChannel:
    def __init__(self):
        self.snapshot = None
        self.sent = []
        self.created = []
        self.result = "sent"

    def create_room(self, principal, applications, operation_id):
        self.created.append(operation_id)
        self.snapshot = RoomSnapshot(room_id="synthetic-room", human_subjects=(principal.account.subject,),
                                     application_ids=applications, private=True, complete=True,
                                     management_restricted=True, history_restricted=True,
                                     configuration_version="synthetic-config-1")
        return self.snapshot.room_id

    def inspect_room(self, room_id):
        return self.snapshot

    def send(self, delivery, text):
        self.sent.append((delivery, text))
        return TransportResult(status=self.result, message_id="message-" + delivery.id if self.result == "sent" else "")


class SyntheticGenerator:
    def __init__(self):
        self.calls = []
        self.after_send = lambda request: None

    def generate(self, request, before_send):
        before_send()
        self.calls.append(request)
        self.after_send(request)
        targets = tuple(item.id for item in request.previous if item.actor != request.actor and item.kind == "opinion")[:1]
        opinions = tuple(item for item in request.previous if item.kind == "opinion")[:2]
        findings = ()
        if request.stage == "comparison":
            findings = (ComparisonFinding(decision="Select exactly one option",
                shared_condition="Only one option can be selected", relationship="conflict",
                stances=tuple(ComparisonStance(artifact_id=item.id, quote=item.text) for item in opinions),
                rationale="One mentor selects A only, the other selects B only."),)
        text = "Synthetic evidence-bounded answer. " + (
            "Choose option A only." if request.actor == "gentle_reviewer" else "Choose option B only.")
        refs = tuple(source.id for source in request.background)
        refs += tuple(item.id for item in opinions) if findings else ()
        return Generation(text=text, claims=("Synthetic claim",),
                          responds_to=targets if request.stage == "debate" else (),
                          source_refs=refs, comparison_findings=findings,
                          debate_needed=True if request.stage == "comparison" else None,
                          uncertainties=("Synthetic missing condition",), next_steps=("Synthetic next step",))


@pytest.fixture
def system(tmp_path):
    store = MentorStore(tmp_path / "mentor.sqlite3")
    identity = IdentityService(store, PersonaRegistry())
    principal = identity.register_principal(Account(platform="test", tenant="tenant", subject="subject-1"), "legacy-owner")
    actors = ("host", "gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")
    applications = {actor: identity.register_application(Application(platform="test", tenant="tenant", external_id=actor,
                     persona_id=actor, role="host" if actor == "host" else "mentor")) for actor in actors}
    message = Envelope(delivery_id="delivery-1", message_id="message-1", external_user_id="open-host",
                       subject="subject-1", external_chat_id="private-host", chat_type="p2p", text="Synthetic question")
    _, _, binding = identity.resolve(applications["host"].id, message)
    channel = SyntheticChannel()
    policy = DiscussionPolicy(store, NoSources(), channel)
    service = DiscussionService(store, identity, policy)
    generator = SyntheticGenerator()
    worker = DiscussionWorker(service, generator)
    dispatcher = OutboxDispatcher(service)
    return service, worker, dispatcher, channel, generator, principal, applications, binding


def prepare(system, mode="debate"):
    service, _, _, _, _, principal, _, binding = system
    conversation = service.create(binding, "Synthetic choice", personas=("gentle_reviewer", "blunt_coach"), mode=mode)
    preview = service.share_preview(conversation.id, principal.id)
    service.apply(Command(id="share-" + conversation.id, principal_id=principal.id, conversation_id=conversation.id,
                          kind="share", expected_revision=1, preview_hash=preview["preview_hash"]))
    return service.provision(conversation.id)


def drain(system, conversation_id):
    service, worker, dispatcher, *_ = system
    for _ in range(60):
        worked = worker.run_one(conversation_id)
        sent = dispatcher.dispatch_one(conversation_id)
        if not worked and not sent:
            break
    else:
        pytest.fail("discussion did not terminate")
    return service.store.read("conversation", conversation_id, Conversation)


def test_same_verified_person_across_apps_keeps_memory_owner(system):
    service, _, _, _, _, principal, apps, _ = system
    message = Envelope(delivery_id="e", message_id="m", external_user_id="open-mentor",
                       subject="subject-1", external_chat_id="private-mentor", chat_type="p2p", text="Hello")
    resolved, _, _ = service.identity.resolve(apps["blunt_coach"].id, message)
    assert resolved.id == principal.id and resolved.legacy_owner_key == "legacy-owner"
    with pytest.raises(MentorError, match="identity_verification_required"):
        service.identity.resolve(apps["blunt_coach"].id, message.model_copy(update={"subject": "stranger"}))


def test_independent_views_real_targets_synthesis_and_distinct_delivery_actors(system):
    conversation = prepare(system)
    completed = drain(system, conversation.id)
    _, _, _, channel, generator, *_ = system
    assert completed.status == "completed"
    assert len(generator.calls) == 8
    assert all(not call.previous for call in generator.calls if call.stage == "opinion")
    assert len([call for call in generator.calls if call.stage == "debate"]) == 4
    assert [delivery.sequence for delivery, _ in channel.sent] == list(range(1, 9))
    assert len({delivery.application_id for delivery, _ in channel.sent}) == 3


def test_reference_upgrade_reuses_opinions_and_one_budget(system):
    conversation = prepare(system, "reference")
    assert drain(system, conversation.id).status == "completed"
    service, _, _, _, generator, principal, *_ = system
    before = service.budgets.status(conversation)["total_requests"]
    command = Command(id="upgrade", principal_id=principal.id, conversation_id=conversation.id, kind="debate", expected_revision=1)
    service.apply(command)
    assert service.apply(command).deduplicated
    assert drain(system, conversation.id).status == "completed"
    assert before == 3
    assert len([call for call in generator.calls if call.stage == "opinion"]) == 2
    assert service.budgets.status(conversation)["total_requests"] == 8


def test_stop_during_generation_discards_late_result_and_outbox(system):
    conversation = prepare(system)
    service, worker, dispatcher, channel, generator, principal, *_ = system
    generator.after_send = lambda _: service.apply(Command(id="stop", principal_id=principal.id,
        conversation_id=conversation.id, kind="stop", expected_revision=0))
    worker.run_one(conversation.id)
    assert service.get(conversation.id, principal.id).status == "stopped"
    assert all(item.kind == "user" for item in service.store.list("artifact", conversation.id, Artifact))
    assert not dispatcher.dispatch_one(conversation.id)
    assert not channel.sent


def test_member_change_before_send_blocks_personal_output(system):
    conversation = prepare(system)
    service, worker, dispatcher, channel, _, principal, *_ = system
    worker.run_one(conversation.id)
    channel.snapshot = channel.snapshot.model_copy(update={"human_subjects": ("subject-1", "stranger")})
    dispatcher.dispatch_one(conversation.id)
    assert not channel.sent
    assert service.get(conversation.id, principal.id).room_status == "sealed"


def test_unverified_room_receives_no_personal_material(system):
    service, _, _, channel, _, principal, _, binding = system
    original = channel.create_room
    def incomplete(*args):
        room_id = original(*args)
        channel.snapshot = channel.snapshot.model_copy(update={"complete": False})
        return room_id
    channel.create_room = incomplete
    with pytest.raises(MentorError, match="room_not_verified"):
        prepare(system)
    assert not channel.sent


def test_unknown_delivery_stops_following_messages(system):
    conversation = prepare(system)
    service, worker, dispatcher, channel, _, principal, *_ = system
    worker.run_one(conversation.id)
    worker.run_one(conversation.id)
    channel.result = "unknown"
    dispatcher.dispatch_one(conversation.id)
    assert not dispatcher.dispatch_one(conversation.id)
    assert len(channel.sent) == 1
    assert service.get(conversation.id, principal.id).status == "interrupted"


def test_fixed_private_window_can_select_multiple_independent_questions(system):
    service, _, _, _, _, principal, apps, _ = system
    message = Envelope(delivery_id="e", message_id="m", external_user_id="open-mentor", subject="subject-1",
                       external_chat_id="private-mentor", chat_type="p2p", text="Hello")
    _, _, binding = service.identity.resolve(apps["gentle_reviewer"].id, message)
    first = service.create(binding, "First question", personas=("gentle_reviewer",))
    second = service.create(binding, "Second question", personas=("gentle_reviewer",))
    assert first.id != second.id
    assert service.identity.current_conversation(binding, "gentle_reviewer") == second.id
    service.identity.select_conversation(binding, "gentle_reviewer", first.id)
    assert service.identity.current_conversation(binding, "gentle_reviewer") == first.id
    with pytest.raises(MentorError, match="fixed_persona_required"):
        service.create(binding, "Wrong persona", personas=("blunt_coach",))


def test_langgraph_checkpoints_exclude_question_and_outputs(system, tmp_path, monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from riji_agent.mentors.langgraph_adapter import LangGraphDriver
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    service, worker, *_ = system
    graph = LangGraphDriver(service.store, tmp_path / "checkpoints.sqlite3")
    worker.execution_driver = graph
    conversation = prepare(system)
    assert drain(system, conversation.id).status == "completed"
    raw = (tmp_path / "checkpoints.sqlite3").read_bytes()
    assert b"Synthetic choice" not in raw
    assert b"Synthetic evidence-bounded answer" not in raw
    graph.forget(conversation.id)
    assert graph.connection.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0
    graph.close()


def test_deleted_history_cannot_return_through_export(system):
    from riji_agent.mentors.history import DiscussionHistory
    service, *_rest, principal, apps, binding = system
    conversation = prepare(system)
    drain(system, conversation.id)
    history = DiscussionHistory(service)
    package = history.export(conversation.id, principal.id)
    encoded = str(package)
    assert "grant_id" not in encoded and "room_id" not in encoded and "snapshot" not in encoded
    history.delete(conversation.id, principal.id, "delete-now")
    assert not service.store.list("artifact", conversation.id, Artifact)
    with pytest.raises(MentorError, match="deleted_discussion_cannot_restore"):
        history.restore(package, principal.id, apply=True)
    with pytest.raises(MentorError, match="discussion_not_found"):
        history.read(conversation.id, principal.id)


def test_source_revocation_blocks_model_and_hides_derived_history(system):
    from riji_agent.mentors.history import DiscussionHistory
    service, worker, dispatcher, channel, generator, principal, *_ = system
    class Sources:
        enabled = True
        def background(self, principal, conversation):
            return (Source(id="source-one", owner_id=principal.id, version="version-one", text="Synthetic source",
                           kind="memory", allowed_personas=conversation.personas),)
        def validate(self, principal, source):
            return self.enabled
    sources = Sources()
    service.policy.sources = sources
    conversation = prepare(system)
    worker.run_one(conversation.id)
    sources.enabled = False
    dispatcher.dispatch_one(conversation.id)
    assert not channel.sent
    assert service.get(conversation.id, principal.id).status == "interrupted"
    view = DiscussionHistory(service).read(conversation.id, principal.id)
    assert any(item.get("unavailable") for item in view["artifacts"])


def test_input_revision_cancels_late_generation(system):
    service, worker, dispatcher, channel, generator, principal, *_ = system
    conversation = prepare(system)
    generator.after_send = lambda request: service.apply(Command(id="supplement-one", principal_id=principal.id,
        conversation_id=conversation.id, expected_revision=1, kind="supplement", text="Synthetic new constraint"))
    worker.run_one(conversation.id)
    assert service.get(conversation.id, principal.id).input_revision == 2
    assert all(item.kind == "user" for item in service.store.list("artifact", conversation.id, Artifact))
    assert not channel.sent


def test_generation_format_repair_is_bounded_and_charged(system):
    service, worker, dispatcher, channel, generator, principal, *_ = system
    conversation = prepare(system)
    def invalid(request, before_send):
        before_send()
        raise ValueError("synthetic invalid JSON")
    generator.generate = invalid
    worker.run_one(conversation.id)
    assert service.budgets.status(conversation)["total_requests"] == 2
    assert service.get(conversation.id, principal.id).status == "interrupted"
    assert not worker.run_one(conversation.id)


def test_unknown_model_result_needs_reconciliation_on_resume(system):
    service, worker, dispatcher, channel, generator, principal, *_ = system
    conversation = prepare(system)
    def uncertain(request, before_send):
        before_send()
        raise TimeoutError("synthetic timeout")
    generator.generate = uncertain
    worker.run_one(conversation.id)
    with pytest.raises(MentorError, match="step_reconciliation_required"):
        service.apply(Command(id="resume-unknown", principal_id=principal.id, conversation_id=conversation.id,
                              kind="resume", expected_revision=1))
    assert service.budgets.status(conversation)["total_requests"] == 1


def test_ingress_deduplicates_message_across_event_redelivery(system):
    from riji_agent.mentors.history import DiscussionHistory
    from riji_agent.mentors.ingress import DiscussionIngress
    service, worker, _, _, _, principal, apps, binding = system
    ingress = DiscussionIngress(service, worker, DiscussionHistory(service))
    message = Envelope(delivery_id="event-one", message_id="private-message", external_user_id="open-blunt",
        subject="subject-1", external_chat_id="blunt-private", chat_type="p2p", text="Synthetic direct question")
    first = ingress.receive(apps["blunt_coach"].id, message)
    second = ingress.receive(apps["blunt_coach"].id, message.model_copy(update={"delivery_id": "event-two"}))
    assert first["conversation_id"] == second["conversation_id"]
    assert second["status"] == "duplicate"
    assert len(service.store.list("conversation", principal.id, Conversation)) == 1


def test_ingress_rejects_unmanaged_groups_and_nonhost_receivers(system):
    from riji_agent.mentors.history import DiscussionHistory
    from riji_agent.mentors.ingress import DiscussionIngress
    service, worker, _, _, _, principal, apps, binding = system
    ingress = DiscussionIngress(service, worker, DiscussionHistory(service))
    message = Envelope(delivery_id="e", message_id="m", external_user_id="open-host", subject="subject-1",
                       external_chat_id="unknown-room", chat_type="group", text="/历史")
    with pytest.raises(MentorError, match="unmanaged_group"):
        ingress.receive(apps["host"].id, message)
    with pytest.raises(MentorError, match="host_is_only_group_receiver"):
        ingress.receive(apps["blunt_coach"].id, message)
    assert not service.store.list("conversation", principal.id, Conversation)


def test_interrupted_incoming_handler_requires_reconciliation_after_restart(system, monkeypatch):
    from riji_agent.mentors.history import DiscussionHistory
    from riji_agent.mentors.ingress import DiscussionIngress
    service, worker, _, _, _, principal, apps, binding = system
    ingress = DiscussionIngress(service, worker, DiscussionHistory(service))
    message = Envelope(delivery_id="event", message_id="interrupted-message", external_user_id="open-blunt",
        subject="subject-1", external_chat_id="blunt-private", chat_type="p2p", text="Synthetic interrupted question")
    def fail(*args):
        raise RuntimeError("Synthetic interrupted operation")
    monkeypatch.setattr(ingress, "_handle", fail)
    with pytest.raises(RuntimeError):
        ingress.receive(apps["blunt_coach"].id, message)
    restarted = DiscussionIngress(service, worker, DiscussionHistory(service))
    with pytest.raises(MentorError, match="incoming_reconciliation_required"):
        restarted.receive(apps["blunt_coach"].id, message.model_copy(update={"delivery_id": "redelivery"}))
    assert not service.store.list("conversation", principal.id, Conversation)


def test_reference_upgrade_does_not_reset_call_budget(system):
    service, worker, dispatcher, channel, generator, principal, *_ = system
    conversation = prepare(system, mode="reference")
    drain(system, conversation.id)
    with service.store.transaction() as db:
        db.execute("UPDATE mentor_budgets SET requests=23 WHERE id=?", (conversation.id,))
    service.apply(Command(id="debate-budget", principal_id=principal.id, conversation_id=conversation.id,
                          expected_revision=1, kind="debate"))
    final = drain(system, conversation.id)
    assert final.status == "completed"
    assert generator.calls[-1].stage == "synthesis"
    assert service.budgets.status(final)["total_requests"] == 24


def test_stop_can_resume_after_late_result_is_discarded(system):
    service, worker, dispatcher, channel, generator, principal, *_ = system
    conversation = prepare(system)
    generator.after_send = lambda request: service.apply(Command(id="stop-before-late", principal_id=principal.id,
        conversation_id=conversation.id, expected_revision=0, kind="stop"))
    worker.run_one(conversation.id)
    generator.after_send = lambda request: None
    service.apply(Command(id="resume-after-late", principal_id=principal.id,
                          conversation_id=conversation.id, expected_revision=1, kind="resume"))
    assert drain(system, conversation.id).status == "completed"


def test_journal_handoff_requires_private_host_preview_then_confirmation(system, tmp_path):
    from riji_agent.drafts.service import DraftService
    from riji_agent.drafts.store import DraftStore
    from riji_agent.journal.index import JournalIndex
    from riji_agent.mentors.history import DiscussionHistory
    from riji_agent.mentors.handoff import JournalHandoff
    service, worker, dispatcher, channel, generator, principal, apps, binding = system
    root = tmp_path / "synthetic-vault"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text("# {{date}}\n\n## 🧠 Notes\n")
    index = JournalIndex(database_path=tmp_path / "index.sqlite3", journal_root=root)
    store = DraftStore(tmp_path / "drafts.sqlite3")
    drafts = DraftService(store, root, index)
    handoffs = JournalHandoff(DiscussionHistory(service), drafts)
    conversation = prepare(system)
    drain(system, conversation.id)
    artifact = next(item for item in service.store.list("artifact", conversation.id, Artifact) if item.kind == "synthesis")
    handoff = handoffs.create(conversation.id, principal.id, (artifact.id,))
    assert not list(root.glob("daily/*.md"))
    with pytest.raises(MentorError, match="private_preview_required"):
        handoffs.confirm(handoff.id, binding, "early-confirm")
    with pytest.raises(MentorError, match="verified_riji_private_chat_required"):
        handoffs.preview(handoff.id, binding.model_copy(update={"chat_type": "group"}), "group-preview")
    preview = handoffs.preview(handoff.id, binding, "private-preview")
    assert "Synthetic evidence" in preview
    assert not list(root.glob("daily/*.md"))
    assert "已按确认内容保存" in handoffs.confirm(handoff.id, binding, "confirm-after-preview")
    assert len(list(root.glob("daily/*.md"))) == 1
    handoffs.history.delete(conversation.id, principal.id, "delete-source-discussion")
    assert len(list(root.glob("daily/*.md"))) == 1  # Explicitly committed copy is independent.
    store.close()
    index.close()
