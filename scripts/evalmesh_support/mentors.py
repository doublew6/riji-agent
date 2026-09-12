"""Isolated mentor evaluation through the actual service, worker and outbox.

Only synthetic input enters this module. Expected answers and review rubrics are
not part of this interface; observations describe persisted execution evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

from riji_agent.mentors.delivery import OutboxDispatcher
from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.local_channel import LocalChannel
from riji_agent.mentors.models import (
    Account, Application, Artifact, ChatBinding, Command, Conversation, Delivery,
    DiscussionRun, Envelope, Generation, GenerationRequest, MentorError,
    Principal, Source, WorkingSummary,
)
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import MentorStore
from riji_agent.mentors.worker import DiscussionWorker
from riji_agent.models.types import LLMProvider
from riji_agent.personas.registry import PersonaRegistry

PERSONAS = ("gentle_reviewer", "blunt_coach", "future_self", "wang_yangming")
COMMAND_FIELDS = {
    "kind", "text", "actor", "mode", "rounds", "personas", "statement_kind",
    "replace_background", "reanalyze",
}


@dataclass
class ScenarioClock:
    """A fixed synthetic message date with real elapsed model time."""

    epoch: float = 1789084800.0
    started: float = field(default_factory=time.monotonic)
    offset: float = 0

    def __call__(self) -> float:
        return self.epoch + self.offset + time.monotonic() - self.started


class SyntheticSources:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        if len(entries) > 8:
            raise ValueError("mentor_eval_too_many_sources")
        self.entries = entries

    def background(self, owner: Principal, conversation: Conversation) -> tuple[Source, ...]:
        return tuple(Source(
            id=entry["id"], owner_id=owner.id, version=entry.get("version", "v1"),
            text=entry["text"], kind=entry.get("kind", "shared_excerpt"),
            allowed_personas=tuple(entry.get("allowed_personas", conversation.personas)),
            content_kind=entry.get("content_kind", "user_statement"),
        ) for entry in self.entries)

    def validate(self, owner: Principal, source: Source) -> bool:
        return source.owner_id == owner.id and any(
            entry["id"] == source.id and entry.get("version", "v1") == source.version
            for entry in self.entries
        )


class RecordingGeneration:
    """Observe requests without replacing production generation or validation."""

    def __init__(self, provider: LLMProvider, personas: PersonaRegistry) -> None:
        self.delegate = ModelGeneration(provider, personas)
        self.requests: list[GenerationRequest] = []
        self.durations: list[float] = []

    def generate(self, request: GenerationRequest, before_send: Callable[[], None]) -> Generation:
        self.requests.append(request)
        started = time.monotonic()
        try:
            return self.delegate.generate(request, before_send)
        finally:
            self.durations.append(time.monotonic() - started)


@dataclass
class Harness:
    service: DiscussionService
    worker: DiscussionWorker
    dispatcher: OutboxDispatcher
    generator: RecordingGeneration
    binding: ChatBinding
    clock: ScenarioClock
    phases: list[dict[str, Any]] = field(default_factory=list)


def _build(case: dict[str, Any], provider: LLMProvider, directory: Path) -> Harness:
    store = MentorStore(directory / "synthetic-mentors.sqlite3")
    identity = IdentityService(store, PersonaRegistry())
    account = Account(platform="local", tenant="eval-synthetic", subject="eval-subject")
    identity.register_principal(account, "eval-owner")
    applications = {actor: identity.register_application(Application(
        platform="local", tenant="eval-synthetic", external_id=actor, persona_id=actor,
        role="host" if actor == "host" else "mentor",
    )) for actor in ("host", *PERSONAS)}
    actor = case.get("persona", "host") if case.get("mode", "private") == "private" else "host"
    _, _, binding = identity.resolve(applications[actor].id, Envelope(
        delivery_id="eval-event", message_id="eval-message", external_user_id="eval-open-id",
        subject=account.subject, external_chat_id="eval-private", chat_type="p2p", text="",
    ))
    clock = ScenarioClock(epoch=float(case.get("now_seconds", 1789084800)))
    channel = LocalChannel(store)
    policy = DiscussionPolicy(store, SyntheticSources(case.get("background", [])), channel, clock)
    service = DiscussionService(store, identity, policy, clock)
    generator = RecordingGeneration(provider, identity.personas)
    return Harness(service, DiscussionWorker(service, generator, clock),
                   OutboxDispatcher(service, clock), generator, binding, clock)


def _drain(harness: Harness, identifier: str) -> None:
    for _ in range(100):
        worked = harness.worker.run_one(identifier)
        sent = harness.dispatcher.dispatch_one(identifier)
        if not worked and not sent:
            return
    raise RuntimeError("mentor_eval_step_limit")


def _phase(harness: Harness, identifier: str, action: str, before: int) -> None:
    current = harness.service.get(identifier, harness.binding.principal_id)
    budget = harness.service.budgets.status(current)
    requests = harness.generator.requests[before:]
    harness.phases.append({
        "action": action, "status": current.status, "run_id": current.run_id,
        "full_run_number": current.run_number, "input_revision": current.input_revision,
        "summary_status": current.summary_status, "correction_version": current.correction_version,
        "requests": len(requests), "actors": [request.actor for request in requests],
        "stages": [request.stage for request in requests], "budget_count": len(budget["budgets"]),
        "total_requests": budget["total_requests"], "virtual_time": harness.clock(),
    })


def _create(harness: Harness, case: dict[str, Any]) -> Conversation:
    current = harness.service.create(
        harness.binding, case["question"],
        personas=tuple(case.get("personas", [case.get("persona", "gentle_reviewer")])),
        mode=case.get("mode", "private"), rounds=int(case.get("rounds", 1)),
        run_personas=tuple(case.get("run_personas", [])),
    )
    if current.kind == "roundtable":
        preview = harness.service.share_preview(current.id, current.owner_id)
        harness.service.apply(Command(
            id="eval-share", principal_id=current.owner_id, conversation_id=current.id,
            kind="share", expected_revision=current.input_revision,
            preview_hash=preview["preview_hash"],
        ))
    _drain(harness, current.id)
    _phase(harness, current.id, "initial", 0)
    return harness.service.get(current.id, current.owner_id)


def _step(harness: Harness, identifier: str, step: dict[str, Any], number: int) -> None:
    before = len(harness.generator.requests)
    current = harness.service.get(identifier, harness.binding.principal_id)
    advance = float(step.get("advance_seconds", 0))
    if not 0 <= advance <= 7 * 86400 or current.status not in {"completed", "partial", "stopped", "archived"}:
        if advance:
            raise ValueError("mentor_eval_invalid_time_advance")
    harness.clock.offset += advance
    fields = {key: value for key, value in step.items() if key in COMMAND_FIELDS}
    if "supersedes_user_index" in step:
        users = [item for item in harness.service.store.list("artifact", identifier, Artifact)
                 if item.kind == "user"]
        fields["supersedes"] = (users[int(step["supersedes_user_index"])].id,)
    harness.service.apply(Command(
        id=f"eval-step-{number}", principal_id=current.owner_id, conversation_id=identifier,
        expected_revision=current.input_revision, **fields,
    ))
    if step.get("drain", True):
        _drain(harness, identifier)
    _phase(harness, identifier, step["kind"], before)


def _observed(harness: Harness, current: Conversation) -> dict[str, Any]:
    store = harness.service.store
    artifacts = store.list("artifact", current.id, Artifact)
    deliveries = store.list("delivery", current.id, Delivery)
    summary = store.read("summary", current.summary_id, WorkingSummary) if current.summary_id else None
    requests = harness.generator.requests
    with store.transaction() as db:
        superseded = harness.service.summaries.superseded(db, current)
        room_count = db.execute("SELECT COUNT(*) FROM mentor_records WHERE kind='local_room'").fetchone()[0]
    return {
        "statuses": [phase["status"] for phase in harness.phases],
        "participants": list(current.personas), "full_runs": current.run_number,
        "budget_count": len(harness.service.budgets.status(current)["budgets"]),
        "correction_version": current.correction_version,
        "summary_user_kinds": sorted({item.kind for item in summary.items if item.kind.startswith("user_")}) if summary else [],
        "superseded_user_count": sum(item.id in superseded for item in artifacts if item.kind == "user"),
        "independent_opinion_peer_contexts": sum(bool(request.previous) for request in requests if request.stage == "opinion"),
        "reanalyze_ai_context_items": sum(item.kind in {"ai_advice", "unresolved"}
                                          for request in requests if request.conversation.reanalyze
                                          for item in (request.working_summary.items if request.working_summary else ())),
        "unmarked_ai_artifacts": sum(item.origin_kind != "ai_discussion" for item in artifacts if item.kind != "user"),
        "undelivered_artifacts": sum(item.status != "sent" for item in deliveries),
        "duplicate_deliveries": len(deliveries) - len({item.artifact_id for item in deliveries}),
        "local_room_count": room_count,
        "private_followup_missing_initial_context": _missing_private_context(requests, artifacts),
    }


def _missing_private_context(requests: list[GenerationRequest], artifacts: tuple[Artifact, ...]) -> int:
    initial = next(item for item in artifacts if item.kind == "user")
    missing = 0
    for request in requests:
        if request.conversation.kind != "private" or request.conversation.run_kind != "followup":
            continue
        material = [request.conversation.question]
        material.extend(item.text for item in request.previous if item.kind == "user")
        if request.working_summary is not None:
            material.extend(item.text for item in request.working_summary.items if item.kind.startswith("user_"))
        missing += not any(initial.text in text for text in material)
    return missing


def _request_evidence(request: GenerationRequest) -> dict[str, Any]:
    return {
        "actor": request.actor, "stage": request.stage, "round": request.round_index,
        "run_id": request.conversation.run_id, "question": request.conversation.question,
        "input_revision": request.conversation.input_revision, "repair_hint": request.repair_hint,
        "reanalyze": request.conversation.reanalyze,
        "background": [item.model_dump(mode="json") for item in request.background],
        "previous": [item.model_dump(mode="json") for item in request.previous],
        "working_summary": request.working_summary.model_dump(mode="json") if request.working_summary else None,
    }


def _result(harness: Harness, current: Conversation, error: str) -> dict[str, Any]:
    store = harness.service.store
    artifacts = store.list("artifact", current.id, Artifact)
    budget = harness.service.budgets.status(current)
    with store.transaction() as db:
        blocked = store.lookup(db, "blocked", current.id)
        steps = [dict(row) for row in db.execute(
            "SELECT status,error,artifact_id FROM mentor_steps WHERE conversation_id=? ORDER BY rowid", (current.id,)
        )]
    return {"output": {
        "observed": _observed(harness, current), "status": current.status,
        "error_code": error, "blocked": blocked, "semantic_review": "pending",
        "phases": harness.phases, "budgets": budget,
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "requests": [_request_evidence(request) for request in harness.generator.requests],
        "runs": [item.model_dump(mode="json") for item in store.list("run", current.id, DiscussionRun)],
        "summaries": [item.model_dump(mode="json") for item in store.list("summary", current.id, WorkingSummary)],
        "deliveries": [item.model_dump(mode="json") for item in store.list("delivery", current.id, Delivery)],
        "step_records": steps,
    }, "metrics": {
        "model_requests": len(harness.generator.requests), "charged_requests": budget["total_requests"],
        "model_seconds": sum(harness.generator.durations), "artifact_count": len(artifacts),
        "output_characters": sum(len(item.text) for item in artifacts if item.kind != "user"),
        "background_characters": sum(len(item.text) for request in harness.generator.requests for item in request.background),
        "repair_attempts": sum(bool(request.repair_hint) for request in harness.generator.requests),
    }}


def evaluate(case: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    """Execute one bounded case from input only; never construct a live provider."""
    if case.get("family") != "mentor" or not isinstance(case.get("question"), str):
        raise ValueError("invalid_mentor_eval_input")
    if len(case.get("steps", [])) > 6:
        raise ValueError("mentor_eval_too_many_steps")
    with tempfile.TemporaryDirectory(prefix="riji-evalmesh-mentor-") as temporary:
        harness = _build(case, provider, Path(temporary))
        current = _create(harness, case)
        error = ""
        for number, step in enumerate(case.get("steps", []), start=1):
            try:
                _step(harness, current.id, step, number)
            except MentorError as exc:
                error = exc.code
                _phase(harness, current.id, step["kind"], len(harness.generator.requests))
                break
        current = harness.service.get(current.id, current.owner_id)
        return _result(harness, current, error)
