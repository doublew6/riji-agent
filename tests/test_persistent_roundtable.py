"""Deterministic persistent-problem contracts, with no external service claims."""

import pytest

from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.models import Artifact, Command, Conversation, MentorError, WorkingSummary
from riji_agent.mentors.summary import MAX_CONTEXT_CHARS
from test_mentor_discussions import drain, prepare, system  # noqa: F401


def apply(system, conversation, kind, **fields):
    service, _, _, _, _, principal, *_ = system
    current = service.get(conversation.id, principal.id)
    receipt = service.apply(Command(id=kind + "-" + str(current.state_revision), principal_id=principal.id,
        conversation_id=current.id, kind=kind, expected_revision=current.input_revision, **fields))
    return service.get(receipt.conversation_id, principal.id)


def test_three_full_runs_reuse_one_room_with_separate_budgets(system):
    current = prepare(system, "reference")
    first = drain(system, current.id)
    second = apply(system, first, "start_run", mode="reference")
    assert second.run_id != first.run_id and second.run_number == 2
    assert drain(system, current.id).status == "completed"
    third = apply(system, second, "start_run", mode="debate", rounds=1)
    assert drain(system, current.id).status == "completed"
    service, _, _, channel, generator, principal, *_ = system
    assert len(channel.created) == 1 and first.room_id == third.room_id
    assert len(service.budgets.status(third)["budgets"]) == 3
    assert len([request for request in generator.calls if request.stage == "opinion"]) == 6
    history = DiscussionHistory(service).read(current.id, principal.id)
    assert len(history["runs"]) == 3
    assert len({item["run_id"] for item in history["artifacts"]}) == 3
    assert all(item["status"] == "completed" for item in history["runs"])


def test_two_speakers_do_not_shrink_four_mentor_audience(system):
    service, _, _, channel, generator, principal, _, binding = system
    personas = ("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")
    current = service.create(binding, "Synthetic choice", personas=personas, mode="reference",
                             run_personas=personas[:2])
    preview = service.share_preview(current.id, principal.id)
    assert preview["personas"] == personas and preview["run_personas"] == personas[:2]
    apply(system, current, "share", preview_hash=preview["preview_hash"])
    service.provision(current.id)
    drain(system, current.id)
    assert len(channel.snapshot.application_ids) == 5
    assert [request.actor for request in generator.calls if request.stage == "opinion"] == list(personas[:2])
    apply(system, current, "start_run", personas=personas[2:], mode="reference")
    drain(system, current.id)
    assert len(channel.created) == 1
    assert [request.actor for request in generator.calls if request.stage == "opinion"][-2:] == list(personas[2:])


def test_plain_followup_only_host_uses_latest_context(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    service, _, _, _, generator, *_ = system
    before = len(generator.calls)
    current = apply(system, current, "supplement", text="I plan to practice daily.", statement_kind="user_plan")
    drain(system, current.id)
    assert len(generator.calls) == before + 1
    request = generator.calls[-1]
    assert request.actor == "host" and request.stage == "followup"
    assert any(item.kind == "user_plan" for item in request.working_summary.items)
    assert any(item.kind == "ai_advice" for item in request.working_summary.items)
    assert service.get(current.id, current.owner_id).run_number == 1


def test_reanalysis_excludes_old_ai_but_retains_user_plan(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    current = apply(system, current, "supplement", text="Plan: twenty minutes.", statement_kind="user_plan")
    drain(system, current.id)
    current = apply(system, current, "reanalyze")
    drain(system, current.id)
    request = system[4].calls[-1]
    assert request.actor == "host" and request.previous == ()
    assert all(item.kind != "ai_advice" for item in request.working_summary.items)
    assert any(item.kind == "user_plan" for item in request.working_summary.items)
    assert request.conversation.reanalyze


def test_correction_suppresses_late_output_and_preserves_marked_archive(system):
    current = prepare(system, "reference")
    service, worker, dispatcher, channel, generator, principal, *_ = system
    initial = service.store.list("artifact", current.id, Artifact)[0]
    generator.after_send = lambda request: apply(system, current, "correct", text="Two hours, not five.", supersedes=(initial.id,))
    worker.run_one(current.id)
    generator.after_send = lambda request: None
    corrected = service.get(current.id, principal.id)
    assert corrected.status == "waiting_user" and corrected.correction_version == 1
    assert not dispatcher.dispatch_one(current.id) and not channel.sent
    apply(system, corrected, "resume")
    drain(system, current.id)
    request = generator.calls[-1]
    assert "Synthetic choice" not in request.conversation.question
    assert any(item.text == "Two hours, not five." for item in request.working_summary.items)
    history = DiscussionHistory(service).read(current.id, principal.id)
    assert next(item for item in history["artifacts"] if item["id"] == initial.id)["superseded"]
    assert history["current_summary"]["correction_version"] == 1


def test_correction_requires_target_or_explicit_background_replacement(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    with pytest.raises(MentorError, match="correction_target_required"):
        apply(system, current, "correct", text="Two hours.")
    updated = apply(system, current, "correct", text="The complete current background: two hours.", replace_background=True)
    assert updated.correction_version == 1


def test_quoted_or_hypothetical_text_never_upgraded_to_execution_result(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    updated = apply(system, current, "supplement", text='My friend said "I practiced yesterday"; imagine if I did.')
    summary = system[0].store.read("summary", updated.summary_id, WorkingSummary)
    assert next(item for item in summary.items if "My friend said" in item.text).kind == "user_statement"
    assert all(item.kind != "user_feedback" for item in summary.items)


def test_active_full_run_rejects_parallel_start_without_new_budget(system):
    current = prepare(system)
    with pytest.raises(MentorError, match="discussion_still_running"):
        apply(system, current, "start_run")
    assert len(system[0].budgets.status(current)["budgets"]) == 1


def test_explicit_side_question_pauses_full_run_then_resumes_same_budget(system):
    current = prepare(system)
    service, worker, _, _, generator, *_ = system
    worker.run_one(current.id)
    followup = apply(system, current, "followup", text="Please explain the tradeoff.", actor="blunt_coach")
    assert followup.suspended_run_id == current.run_id
    before = len(generator.calls)
    completed = drain(system, current.id)
    assert len(generator.calls) == before + 1 and generator.calls[-1].actor == "blunt_coach"
    assert completed.suspended_run_id == current.run_id
    with pytest.raises(MentorError, match="discussion_resume_required"):
        apply(system, current, "start_run")
    restored = apply(system, completed, "resume")
    assert restored.run_id == current.run_id and not restored.suspended_run_id
    drain(system, current.id)
    assert len(service.budgets.status(restored)["budgets"]) == 2


def test_summary_overflow_is_pending_and_explicit_replacement_recovers(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    current = apply(system, current, "supplement", text="A" * 9900)
    drain(system, current.id)
    current = apply(system, current, "supplement", text="B" * 9900)
    assert current.summary_status == "pending" and current.status == "waiting_user"
    assert not system[1].run_one(current.id)
    with pytest.raises(MentorError, match="summary_refresh_required"):
        apply(system, current, "resume")
    current = apply(system, current, "correct", text="Complete replacement background.", replace_background=True)
    assert current.summary_status == "current"
    apply(system, current, "resume")
    drain(system, current.id)
    request = system[4].calls[-1]
    assert sum(len(item.text) for item in request.working_summary.items) < MAX_CONTEXT_CHARS
    assert request.conversation.question == "Complete replacement background."


def test_archive_restore_delete_and_export_preserve_boundaries(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    service, _, _, channel, _, principal, *_ = system
    archived = apply(system, current, "archive")
    assert archived.status == "archived"
    with pytest.raises(MentorError, match="problem_archived"):
        apply(system, archived, "supplement", text="Hello")
    history = DiscussionHistory(service)
    channel.snapshot = None
    package = history.export(current.id, principal.id)
    assert package["payload"]["version"] == 2
    assert package["payload"]["summaries"] and package["payload"]["runs"]
    assert "grant_id" not in package["payload"]["conversation"]
    history.delete(current.id, principal.id, "delete-problem")
    assert not service.store.list("summary", current.id, WorkingSummary)
    with pytest.raises(MentorError, match="deleted_discussion_cannot_restore"):
        history.restore(package, principal.id, apply=True)


def test_source_revocation_redacts_all_summary_versions_and_blocks_generation(system):
    from riji_agent.mentors.models import Source
    service, _, _, _, generator, principal, *_ = system

    class Sources:
        enabled = True

        def background(self, owner, conversation):
            return (Source(id="synthetic-source", owner_id=owner.id, version="v1",
                           text="Synthetic shared fact", kind="memory", allowed_personas=conversation.personas),)

        def validate(self, owner, source):
            return self.enabled

    sources = Sources()
    service.policy.sources = sources
    current = prepare(system, "reference")
    drain(system, current.id)
    assert any(item["kind"] == "ai_advice" for item in DiscussionHistory(service).read(current.id, principal.id)["current_summary"]["items"])
    sources.enabled = False
    before = len(generator.calls)
    apply(system, current, "start_run", mode="reference")
    drain(system, current.id)
    assert len(generator.calls) == before
    history = DiscussionHistory(service).read(current.id, principal.id)
    advice = [item for summary in history["summaries"] for item in summary["items"] if item["kind"] == "ai_advice"]
    assert advice and all(item.get("unavailable") and not item["text"] for item in advice)


def test_export_restore_preserves_typed_summary_and_is_inert(system, tmp_path):
    from riji_agent.mentors.identity import IdentityService
    from riji_agent.mentors.policy import DiscussionPolicy
    from riji_agent.mentors.ports import NoSources
    from riji_agent.mentors.service import DiscussionService
    from riji_agent.mentors.store import MentorStore
    from riji_agent.personas.registry import PersonaRegistry
    from test_mentor_discussions import SyntheticChannel
    current = prepare(system, "reference")
    drain(system, current.id)
    service, _, _, _, _, principal, *_ = system
    initial = service.store.list("artifact", current.id, Artifact)[0]
    apply(system, current, "correct", text="Current facts.", supersedes=(initial.id,))
    drain(system, current.id)
    package = DiscussionHistory(service).export(current.id, principal.id)
    clone = MentorStore(tmp_path / "portable.sqlite3")
    identity = IdentityService(clone, PersonaRegistry())
    owner = identity.register_principal(principal.account, "portable-owner")
    imported = DiscussionService(clone, identity, DiscussionPolicy(clone, NoSources(), SyntheticChannel()))
    history = DiscussionHistory(imported)
    assert not history.restore(package, owner.id)["applied"]
    assert history.restore(package, owner.id, apply=True)["applied"]
    view = history.read(current.id, owner.id)
    assert view["summaries"] and view["runs"] and view["current_summary"]
    assert any(item["superseded"] for item in view["artifacts"])
    assert view["conversation"]["room_status"] == "sealed"
    with pytest.raises(MentorError, match="restored_history_is_read_only"):
        imported.apply(Command(id="forbidden-resume", principal_id=owner.id, conversation_id=current.id,
                               kind="start_run", expected_revision=view["conversation"]["input_revision"]))


def test_legacy_roundtable_loads_summary_without_new_budget_or_generation(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    service, _, _, _, generator, principal, *_ = system
    before = len(generator.calls)
    with service.store.transaction() as db:
        old = service.store.get(db, "conversation", current.id, Conversation)
        old = old.model_copy(update={"summary_id": "", "summary_version": 0})
        service.store.put(db, "conversation", old, principal.id)
        db.execute("DELETE FROM mentor_records WHERE kind='summary' AND owner=?", (current.id,))
    loaded = service.get(current.id, principal.id)
    assert loaded.summary_id and loaded.summary_status == "current"
    assert len(generator.calls) == before
    assert len(service.budgets.status(loaded)["budgets"]) == 1


def test_origin_correction_invalidates_already_transferred_ai_conclusion(system):
    from riji_agent.mentors.transfer import DiscussionTransfer, TransferSources
    service, _, _, _, _, principal, _, binding = system
    service.policy.sources = TransferSources(service.store, service.policy.sources)
    current = prepare(system, "reference")
    drain(system, current.id)
    artifacts = service.store.list("artifact", current.id, Artifact)
    statement = next(item for item in artifacts if item.kind == "user")
    conclusion = next(item for item in artifacts if item.kind == "comparison")
    transfer = DiscussionTransfer(DiscussionHistory(service))
    preview = transfer.preview(current.id, principal.id, (conclusion.id,), current.personas)
    target = service.create(binding, "Related synthetic question", personas=current.personas, mode="reference")
    transfer.accept(preview["id"], principal.id, preview["fingerprint"], target.id)
    target = service.get(target.id, principal.id)
    source = service.policy.background(target)[0]
    assert service.policy.sources.validate(principal, source)
    apply(system, current, "start_run", mode="reference")
    drain(system, current.id)
    assert service.policy.sources.validate(principal, source)
    apply(system, current, "correct", text="Corrected synthetic condition", supersedes=(statement.id,))
    assert not service.policy.sources.validate(principal, source)
    with pytest.raises(MentorError, match="invalid_transfer_selection"):
        transfer.preview(current.id, principal.id, (conclusion.id,), current.personas)


def test_deleted_origin_invalidates_transferred_user_statement(system):
    from riji_agent.mentors.transfer import DiscussionTransfer, TransferSources
    service, _, _, _, _, principal, _, binding = system
    service.policy.sources = TransferSources(service.store, service.policy.sources)
    current = prepare(system, "reference")
    drain(system, current.id)
    statement = service.store.list("artifact", current.id, Artifact)[0]
    transfer = DiscussionTransfer(DiscussionHistory(service))
    preview = transfer.preview(current.id, principal.id, (statement.id,), current.personas)
    target = service.create(binding, "Related question", personas=current.personas, mode="reference")
    transfer.accept(preview["id"], principal.id, preview["fingerprint"], target.id)
    target = service.get(target.id, principal.id)
    source = service.policy.background(target)[0]
    assert service.policy.sources.validate(principal, source)
    DiscussionHistory(service).delete(current.id, principal.id, "delete-origin")
    assert not service.policy.sources.validate(principal, source)


def test_followup_budget_exhaustion_does_not_schedule_extra_summary(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    current = apply(system, current, "followup", text="A single question", actor="blunt_coach")
    service, worker, _, _, generator, *_ = system
    before = len(generator.calls)
    with service.store.transaction() as db:
        db.execute("UPDATE mentor_budgets SET requests=request_limit WHERE id=?", (current.run_id,))
    worker.run_one(current.id)
    latest = service.get(current.id, current.owner_id)
    assert latest.status == "partial" and not latest.summarize_requested
    assert len(generator.calls) == before and not worker.run_one(current.id)


@pytest.mark.parametrize("content_kind,retained", [
    ("ai_discussion", False), ("mixed", False), ("unknown", False),
    ("user_statement", True), ("user_plan", True), ("user_feedback", True),
])
def test_reanalysis_filters_ai_in_transferred_background(system, content_kind, retained):
    from riji_agent.mentors.models import Source
    service, _, _, _, generator, *_ = system

    class Sources:
        def background(self, owner, conversation):
            return (Source(id="synthetic-excerpt", owner_id=owner.id, version="v1",
                           text="Synthetic transferred material", kind="shared_excerpt",
                           allowed_personas=conversation.personas, content_kind=content_kind),)

        def validate(self, owner, source):
            return True

    service.policy.sources = Sources()
    current = prepare(system, "reference")
    drain(system, current.id)
    assert generator.calls[0].background[0].content_kind == content_kind
    apply(system, current, "reanalyze")
    drain(system, current.id)
    assert bool(generator.calls[-1].background) is retained


def test_waiting_for_explicit_context_replacement_does_not_spend_active_time(system):
    current = prepare(system, "reference")
    drain(system, current.id)
    service, worker, dispatcher, _, _, *_ = system
    clock = [10000.0]
    for component in (service, service.budgets, service.policy, worker, dispatcher):
        component.now = lambda: clock[0]
    apply(system, current, "supplement", text="A" * 9900)
    drain(system, current.id)
    current = apply(system, current, "supplement", text="B" * 9900)
    assert current.status == "waiting_user"
    clock[0] += 3600
    current = apply(system, current, "correct", text="Replacement current conditions.", replace_background=True)
    apply(system, current, "resume")
    assert drain(system, current.id).status == "completed"
    assert service.budgets.status(current)["current"]["elapsed"] == 0
