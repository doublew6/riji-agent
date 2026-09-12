"""Evidence attribution, stage ownership, bounded repair and minority continuity."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from riji_agent.mentors.comparison import current_opinions, validate_comparison
from riji_agent.mentors.generation import _previous_payload, output_schema
from riji_agent.mentors.handoff import _artifact_hash
from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.models import (
    Artifact, Command, ComparisonFinding, ComparisonStance, Conversation, Execution,
    Generation, GenerationRequest, MentorError,
)
from riji_agent.mentors.planner import next_stage, visible_history, Stage
from test_mentor_discussions import drain, prepare, system  # noqa: F401


def request(texts: tuple[str, ...] = ("Choose A only.", "Choose B only."),
            stage: str = "comparison", actors: tuple[str, ...] = ()) -> GenerationRequest:
    actors = actors or ("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")[:len(texts)]
    conversation = Conversation(owner_id="owner", question="Only one option is possible.",
        kind="roundtable", mode="debate", personas=actors, run_personas=actors,
        created_at=0, updated_at=0)
    previous = tuple(Artifact(id=f"opinion-{number}", conversation_id=conversation.id,
        actor=actor, kind="opinion", text=text, run_id=conversation.run_id,
        input_revision=conversation.input_revision, created_at=0)
        for number, (actor, text) in enumerate(zip(actors, texts)))
    execution = Execution(conversation_id=conversation.id, run_id=conversation.run_id,
        input_revision=1, cancel_epoch=0, lease_generation=1)
    return GenerationRequest(conversation=conversation, actor="host", stage=stage,
        round_index=0, background=(), previous=previous, execution=execution)


def result(current: GenerationRequest, relationship: str = "conflict",
           positions: tuple[int, int] = (0, 1)) -> Generation:
    selected = tuple(current.previous[index] for index in positions)
    finding = ComparisonFinding(decision="Which option to choose",
        shared_condition="Only one option can be selected", relationship=relationship,
        stances=tuple(ComparisonStance(artifact_id=item.id, quote=item.text) for item in selected),
        rationale="Compare the quoted choices under the same condition.")
    return Generation(text="Synthetic comparison with explicit attributed positions.",
        comparison_findings=(finding,), source_refs=tuple(item.id for item in selected),
        debate_needed=relationship == "conflict")


def comparison_artifact(current: GenerationRequest, output: Generation) -> Artifact:
    return Artifact(conversation_id=current.conversation.id, actor="host", kind="comparison",
        input_revision=current.conversation.input_revision, run_id=current.conversation.run_id,
        created_at=0, **output.model_dump())


@pytest.mark.parametrize("relationship", ["compatible", "complementary", "uncertain"])
def test_nonopposing_evidence_can_close_without_forcing_consensus(relationship: str) -> None:
    current = request(("Try writing after brushing teeth at night.",
        "Try writing after brushing teeth; if evenings repeatedly fail, try mornings."))
    output = result(current, relationship)
    validate_comparison(output, current)
    artifact = comparison_artifact(current, output)
    assert next_stage(current.conversation, (*current.previous, artifact)).kind == "synthesis"
    assert artifact.comparison_findings[0].stances[1].quote.endswith("try mornings.")


def test_true_opposition_enters_debate_and_keeps_independent_first_round() -> None:
    current = request()
    output = result(current)
    validate_comparison(output, current)
    stage = next_stage(current.conversation, (*current.previous, comparison_artifact(current, output)))
    assert stage.kind == "debate"
    assert visible_history(Stage("opinion", "gentle_reviewer"), current.previous, current.conversation) == ()


def test_minority_position_is_preserved_as_real_two_sided_evidence() -> None:
    current = request(("Choose A only.", "Choose A only.", "Choose A only.", "Choose B only."))
    output = result(current, positions=(0, 3))
    validate_comparison(output, current)
    artifact = comparison_artifact(current, output)
    assert artifact.comparison_findings[0].stances[1].artifact_id == current.previous[3].id
    assert next_stage(current.conversation, (*current.previous, artifact)).kind == "debate"
    synthesis_context = visible_history(Stage("synthesis", "host"), (*current.previous, artifact), current.conversation)
    assert current.previous[3] in synthesis_context
    assert artifact in synthesis_context


def test_shared_conditions_and_counterexamples_are_retained() -> None:
    current = request(("With an immovable deadline today, ship A and accept a reversible defect.",
        "Even with today's deadline, delay release if the defect can lose data."))
    output = result(current, "uncertain").model_copy(update={"uncertainties": (
        "The deadline might be negotiable.", "A data-loss defect is a counterexample to shipping."),
        "next_steps": ("Verify data-loss exposure before choosing.",)})
    validate_comparison(output, current)
    artifact = comparison_artifact(current, output)
    assert artifact.uncertainties == output.uncertainties
    assert "if the defect can lose data" in artifact.comparison_findings[0].stances[1].quote


@pytest.mark.parametrize("changes,code", [
    ({"debate_needed": None}, "comparison_decision_required"),
    ({"comparison_findings": ()}, "comparison_decision_inconsistent"),
    ({"source_refs": ()}, "comparison_evidence_invalid"),
    ({"debate_needed": False}, "comparison_decision_inconsistent"),
])
def test_unsupported_decision_is_rejected(changes: dict[str, Any], code: str) -> None:
    current = request()
    with pytest.raises(MentorError, match=code):
        validate_comparison(result(current).model_copy(update=changes), current)


def test_original_unsubstantiated_disagreement_shape_is_rejected() -> None:
    current = request(("Try evenings; adjust if this assumption fails.",
        "Try evenings; if they repeatedly fail, try mornings."))
    original_shape = Generation(text="They substantively disagree about allowing mornings.",
        claims=("The first mentor opposes morning recording.",),
        source_refs=tuple(item.id for item in current.previous), debate_needed=True)
    with pytest.raises(MentorError, match="comparison_decision_inconsistent"):
        validate_comparison(original_shape, current)


@pytest.mark.parametrize("replacement", ["invented", "other-run", "other-revision", "same-actor", "user"])
def test_only_two_distinct_current_opinion_authors_can_support_attribution(replacement: str) -> None:
    current = request()
    output = result(current)
    second = current.previous[1]
    changes = {
        "invented": {"id": "unseen-id"}, "other-run": {"run_id": "old-run"},
        "other-revision": {"input_revision": 2}, "same-actor": {"actor": current.previous[0].actor},
        "user": {"kind": "user"},
    }[replacement]
    current = current.model_copy(update={"previous": (current.previous[0], second.model_copy(update=changes))})
    with pytest.raises(MentorError, match="comparison_evidence_invalid"):
        validate_comparison(output, current)


def test_fabricated_or_paraphrased_quotes_do_not_count_as_exact_evidence() -> None:
    current = request()
    data = result(current).model_dump()
    data["comparison_findings"][0]["stances"][0]["quote"] = "I reject all alternatives."
    with pytest.raises(MentorError, match="comparison_quote_unsupported"):
        validate_comparison(Generation.model_validate(data), current)


@pytest.mark.parametrize("stage", ["opinion", "debate", "synthesis", "followup"])
def test_only_comparison_can_set_a_debate_decision(stage: str) -> None:
    current = request(stage=stage)
    with pytest.raises(MentorError, match="comparison_stage_required"):
        validate_comparison(result(current), current)
    validate_comparison(Generation(text="Independent bounded contribution."), current)
    schema = output_schema(current)
    assert schema["properties"]["debate_needed"]["type"] == "null"
    assert schema["properties"]["comparison_findings"]["maxItems"] == 0


def test_comparison_schema_and_parser_enforce_bounded_explicit_evidence() -> None:
    current = request()
    schema = output_schema(current)
    assert schema["properties"]["debate_needed"] == {"type": "boolean"}
    assert {"comparison_findings", "debate_needed"}.issubset(schema["required"])
    assert schema["$defs"]["ComparisonStance"]["properties"]["artifact_id"]["enum"] == [
        item.id for item in current.previous]
    data = result(current).model_dump()
    for change in ({"debate_needed": "true"}, {"comparison_findings": data["comparison_findings"] * 3}):
        with pytest.raises(ValidationError):
            Generation.model_validate({**data, **change})


def test_no_conflict_can_have_zero_findings_and_still_report_uncertainty() -> None:
    current = request()
    output = Generation(text="No supported conflict is established.", debate_needed=False,
        uncertainties=("The actual schedule is unknown.",))
    validate_comparison(output, current)
    assert output.uncertainties


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_attribution_repair_is_once_and_uses_the_existing_budget(system: Any, repair_succeeds: bool) -> None:
    service, worker, _, _, generator, principal, *_ = system
    conversation = prepare(system)
    worker.run_one(conversation.id)
    worker.run_one(conversation.id)
    original = generator.generate
    attempts = []

    def generate(current: GenerationRequest, guard: Any) -> Generation:
        output = original(current, guard)
        if current.stage != "comparison":
            return output
        attempts.append(current.repair_hint)
        if repair_succeeds and current.repair_hint:
            return output
        return output.model_copy(update={"comparison_findings": ()})

    generator.generate = generate
    worker.run_one(conversation.id)
    assert attempts == ["", "comparison_decision_inconsistent"]
    assert service.budgets.status(conversation)["total_requests"] == 4
    artifacts = service.store.list("artifact", conversation.id, Artifact)
    if repair_succeeds:
        assert next(item for item in artifacts if item.kind == "comparison").comparison_findings
        assert drain(system, conversation.id).status == "completed"
    else:
        assert service.get(conversation.id, principal.id).status == "interrupted"
        assert not any(item.kind == "comparison" for item in artifacts)
        assert not worker.run_one(conversation.id)


def test_legacy_artifacts_and_pending_preview_hashes_remain_compatible() -> None:
    current = request()
    legacy = current.previous[0].model_dump()
    legacy.pop("comparison_findings")
    artifact = Artifact.model_validate(legacy)
    assert artifact.comparison_findings == ()
    assert _artifact_hash(legacy) == _artifact_hash(artifact.model_dump())
    modern = comparison_artifact(current, result(current)).model_dump()
    changed = deepcopy(modern)
    changed["comparison_findings"][0]["rationale"] = "Changed after the preview"
    assert _artifact_hash(modern) != _artifact_hash(changed)


def test_legacy_independent_opinion_decisions_are_not_sent_to_the_host() -> None:
    opinion = request().previous[0].model_copy(update={"debate_needed": True})
    sent = _previous_payload(opinion)
    assert sent["debate_needed"] is None and sent["comparison_findings"] == []
    assert sent["text"] == opinion.text and sent["claims"] == opinion.claims
    assert opinion.debate_needed is True


def test_comparison_evidence_survives_export_restore_with_remapped_ids(system: Any) -> None:
    service, _, _, _, _, principal, *_ = system
    conversation = prepare(system)
    drain(system, conversation.id)
    history = DiscussionHistory(service)
    payload = history.export(conversation.id, principal.id)
    assert next(item for item in payload["payload"]["artifacts"] if item["kind"] == "comparison")["comparison_findings"]
    # Restore is an inert archive in another owner's fresh synthetic store.
    from riji_agent.mentors.store import MentorStore
    from riji_agent.mentors.identity import IdentityService
    from riji_agent.mentors.policy import DiscussionPolicy
    from riji_agent.mentors.ports import NoSources
    from riji_agent.mentors.local_channel import LocalChannel
    from riji_agent.mentors.service import DiscussionService
    from riji_agent.personas.registry import PersonaRegistry
    store = MentorStore(service.store.path.with_name("restored.sqlite3"))
    identity = IdentityService(store, PersonaRegistry())
    owner = identity.register_principal(principal.account, principal.legacy_owner_key)
    restored = DiscussionHistory(DiscussionService(store, identity, DiscussionPolicy(store, NoSources(), LocalChannel(store))))
    receipt = restored.restore(json.loads(json.dumps(payload)), owner.id, apply=True)
    artifacts = store.list("artifact", receipt["conversation_id"], Artifact)
    comparison = next(item for item in artifacts if item.kind == "comparison")
    for stance in comparison.comparison_findings[0].stances:
        opinion = next(item for item in artifacts if item.id == stance.artifact_id)
        assert stance.quote in opinion.text


def test_legacy_initial_opinions_have_the_same_schema_and_validation_scope() -> None:
    current = request()
    legacy = current.model_copy(update={"previous": tuple(
        item.model_copy(update={"run_id": ""}) for item in current.previous)})
    schema = output_schema(legacy)
    assert schema["$defs"]["ComparisonStance"]["properties"]["artifact_id"]["enum"] == [
        item.id for item in legacy.previous]
    validate_comparison(result(legacy), legacy)
    assert next_stage(legacy.conversation, legacy.previous).kind == "comparison"


@pytest.mark.parametrize("changes", [
    {"run_number": 2}, {"run_kind": "followup"}, {"run_kind": "roundtable"},
    {"reanalyze": True}, {"suspended_run_id": "suspended"},
])
def test_unscoped_legacy_opinions_cannot_leak_into_other_run_contexts(changes: dict[str, Any]) -> None:
    current = request()
    current = current.model_copy(update={
        "conversation": current.conversation.model_copy(update=changes),
        "previous": tuple(item.model_copy(update={"run_id": ""}) for item in current.previous),
    })
    assert not current_opinions(current)
    assert output_schema(current)["$defs"]["ComparisonStance"]["properties"]["artifact_id"]["enum"] == []
    with pytest.raises(MentorError, match="comparison_evidence_invalid"):
        validate_comparison(result(current), current)


def test_legacy_allowance_still_rejects_wrong_conversation_and_revision() -> None:
    current = request()
    for changes in ({"conversation_id": "other"}, {"input_revision": 99}, {"run_id": "other-run"}):
        previous = tuple(item.model_copy(update={"run_id": "", **changes}) for item in current.previous)
        assert not current_opinions(current.model_copy(update={"previous": previous}))


def _reopen_discussion_system(previous: Any) -> Any:
    from riji_agent.mentors.delivery import OutboxDispatcher
    from riji_agent.mentors.identity import IdentityService
    from riji_agent.mentors.policy import DiscussionPolicy
    from riji_agent.mentors.ports import NoSources
    from riji_agent.mentors.service import DiscussionService
    from riji_agent.mentors.store import MentorStore
    from riji_agent.mentors.worker import DiscussionWorker
    from riji_agent.personas.registry import PersonaRegistry
    old, _, _, channel, generator, principal, apps, binding = previous
    store = MentorStore(old.store.path)
    identity = IdentityService(store, PersonaRegistry())
    service = DiscussionService(store, identity, DiscussionPolicy(store, NoSources(), channel))
    return (service, DiscussionWorker(service, generator), OutboxDispatcher(service),
            channel, generator, principal, apps, binding)


def test_pending_legacy_initial_comparison_recovers_without_replaying_opinions(system: Any) -> None:
    service, worker, dispatcher, _, generator, principal, *_ = system
    conversation = prepare(system)
    for _ in range(2):
        worker.run_one(conversation.id)
        dispatcher.dispatch_one(conversation.id)
    opinions = [item for item in service.store.list("artifact", conversation.id, Artifact) if item.kind == "opinion"]
    with service.store.transaction() as db:
        for item in opinions:
            legacy = item.model_dump()
            legacy.pop("run_id")
            legacy.pop("comparison_findings")
            db.execute("UPDATE mentor_records SET value=? WHERE kind='artifact' AND id=?",
                       (json.dumps(legacy), item.id))
    reopened = _reopen_discussion_system(system)
    restored, restored_worker, *_ = reopened
    assert restored.recover() == 1
    current = restored.get(conversation.id, principal.id)
    restored.apply(Command(id="resume-legacy", principal_id=principal.id, conversation_id=current.id,
        kind="resume", expected_revision=current.input_revision))
    assert restored_worker.run_one(current.id)
    assert generator.calls[-1].stage == "comparison" and not generator.calls[-1].repair_hint
    assert len(generator.calls) == 3
    comparison = next(item for item in restored.store.list("artifact", current.id, Artifact) if item.kind == "comparison")
    assert {stance.artifact_id for stance in comparison.comparison_findings[0].stances} == {item.id for item in opinions}
    assert drain(reopened, current.id).status == "completed"
    assert len([call for call in generator.calls if call.stage == "opinion"]) == 2
