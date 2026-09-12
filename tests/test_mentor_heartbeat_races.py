"""Late policy checks and failures cannot mutate a replacement execution."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from riji_agent.mentors.models import Artifact, Command, Conversation, Delivery, MentorError, Source
from riji_agent.mentors.store import key
from riji_agent.models.types import LLMError
from test_mentor_discussions import drain, prepare, system  # noqa: F401


class GatedSources:
    def __init__(self, owner: str) -> None:
        self.source = Source(id="synthetic-source", owner_id=owner, version="v1",
            text="Synthetic permitted context.", kind="journal",
            allowed_personas=("gentle_reviewer", "blunt_coach"))
        self.entered = threading.Event()
        self.release = threading.Event()
        self.armed = False

    def background(self, principal: Any, conversation: Any) -> tuple[Source, ...]:
        return (self.source,)

    def validate(self, principal: Any, source: Source) -> bool:
        if not self.armed:
            return True
        self.entered.set()
        assert self.release.wait(5), "synthetic validation barrier was not released"
        return False


def replacement(service: Any, current: Conversation, field: str) -> Conversation:
    value = "replacement-" + field if field in {"run_id", "owner_id"} else getattr(current, field) + 1
    updated = current.model_copy(update={field: value})
    with service.store.transaction() as db:
        service.store.put(db, "conversation", updated, updated.owner_id)
        service.store.bind(db, "blocked", updated.id, "replacement-sentinel")
    return updated


def snapshot(service: Any) -> dict[str, list[tuple[Any, ...]]]:
    queries = {
        "records": "SELECT kind,id,owner,value FROM mentor_records ORDER BY kind,id",
        "keys": "SELECT kind,key,value FROM mentor_keys ORDER BY kind,key",
        "steps": "SELECT * FROM mentor_steps ORDER BY key",
        "budgets": "SELECT * FROM mentor_budgets ORDER BY id",
        "source_usage": "SELECT * FROM mentor_source_usage ORDER BY owner,source_id,version",
    }
    with service.store.transaction() as db:
        return {name: [tuple(row) for row in db.execute(query)] for name, query in queries.items()}


def pending_delivery(service: Any, current: Conversation) -> None:
    artifact = Artifact(conversation_id=current.id, actor="gentle_reviewer", kind="opinion",
        input_revision=current.input_revision, run_id=current.run_id,
        text="Synthetic existing reply.", created_at=service.now())
    delivery = Delivery(conversation_id=current.id, sequence=1, application_id="synthetic-app",
        chat_id=current.room_id, artifact_id=artifact.id, input_revision=current.input_revision,
        cancel_epoch=current.cancel_epoch)
    with service.store.transaction() as db:
        service.store.put(db, "artifact", artifact, current.id)
        service.store.put(db, "delivery", delivery, current.id)


def install_barrier(system: Any, failure: str, monkeypatch: Any) -> tuple[Any, Any, Any]:
    service, worker, _, channel, _, principal, *_ = system
    sources = GatedSources(principal.id)
    if failure == "source":
        service.policy.sources = sources
    current = prepare(system, "reference")
    current, execution = worker._claim(current.id)
    pending_delivery(service, current)
    if failure == "source":
        sources.armed = True
    else:
        original = channel.snapshot

        def inspect(room_id: str) -> Any:
            sources.entered.set()
            assert sources.release.wait(5), "synthetic audience barrier was not released"
            if failure == "audience_unavailable":
                raise OSError("synthetic channel unavailable")
            return original.model_copy(update={"human_subjects": ("subject-1", "new-subject")})

        monkeypatch.setattr(channel, "inspect_room", inspect)
    return current, execution, sources


@pytest.mark.parametrize("failure", ["source", "audience_changed", "audience_unavailable"])
@pytest.mark.parametrize("changed_field", ["run_id", "lease_generation", "owner_id"])
def test_late_policy_failure_cannot_block_or_cancel_replacement(
    system: Any, monkeypatch: Any, failure: str, changed_field: str,
) -> None:
    service = system[0]
    current, execution, gate = install_barrier(system, failure, monkeypatch)
    errors: list[BaseException] = []

    def check() -> None:
        try:
            service.policy.check(execution)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=check, name="synthetic-policy-race", daemon=True)
    thread.start()
    try:
        assert gate.entered.wait(5), "policy did not reach the source/audience boundary"
        replacement(service, current, changed_field)
        expected = snapshot(service)
    finally:
        gate.release.set()
        thread.join(5)
    assert not thread.is_alive(), "policy validation thread leaked"
    assert len(errors) == 1 and isinstance(errors[0], MentorError), errors
    assert snapshot(service) == expected
    assert not system[3].sent and not system[4].calls


@pytest.mark.parametrize("changed_field", ["run_id", "input_revision", "cancel_epoch", "lease_generation", "owner_id"])
@pytest.mark.parametrize("step_status", ["call_sent", "succeeded"])
@pytest.mark.parametrize("late_error", ["model_transport_failed", "budget_exhausted"])
def test_old_failure_cannot_overwrite_replacement_step_or_ledger(
    system: Any, changed_field: str, step_status: str, late_error: str,
) -> None:
    service, worker, *_ = system
    current = prepare(system, "reference")
    current, execution = worker._claim(current.id)
    step = key(current.run_id, str(current.input_revision), "opinion", "0", "gentle_reviewer")
    assert worker._begin_step(current, step)
    replacement(service, current, changed_field)
    with service.store.transaction() as db:
        db.execute("UPDATE mentor_steps SET status=?,artifact_id=?,error=? WHERE key=?",
            (step_status, "replacement-artifact" if step_status == "succeeded" else None,
             "replacement-step-sentinel", step))
    expected = snapshot(service)
    worker._failed(execution, step, late_error)
    assert snapshot(service) == expected
    assert not system[3].sent and not system[4].calls


@pytest.mark.parametrize("uncertain", [False, True])
def test_inactive_cancelled_attempt_only_records_receipt_and_resume_is_explicit(
    system: Any, uncertain: bool,
) -> None:
    service, worker, _, channel, generator, principal, *_ = system
    conversation = prepare(system, "reference")
    stopped_snapshot: dict[str, Any] = {}

    def stop_after_send(request: Any) -> None:
        service.apply(Command(id="stop-late-receipt", principal_id=principal.id,
            conversation_id=conversation.id, expected_revision=0, kind="stop"))
        stopped_snapshot.update(snapshot(service))
        if uncertain:
            raise LLMError("model_transport_failed")

    generator.after_send = stop_after_send
    assert worker.run_one(conversation.id)
    actual = snapshot(service)
    assert {k: v for k, v in actual.items() if k != "steps"} == {
        k: v for k, v in stopped_snapshot.items() if k != "steps"}
    with service.store.transaction() as db:
        steps = db.execute("SELECT status,artifact_id FROM mentor_steps").fetchall()
        artifacts = service.store.records(db, "artifact", conversation.id, Artifact)
        deliveries = service.store.records(db, "delivery", conversation.id, Delivery)
    assert [tuple(row) for row in steps] == [("unknown" if uncertain else "failed", None)]
    assert service.get(conversation.id, principal.id).status == "stopped"
    assert all(item.kind == "user" for item in artifacts) and not deliveries and not channel.sent
    assert service.budgets.status(conversation)["total_requests"] == 1
    assert len(generator.calls) == 1
    generator.after_send = lambda request: None
    resume = Command(id="resume-late-receipt", principal_id=principal.id,
        conversation_id=conversation.id, expected_revision=1, kind="resume")
    if uncertain:
        with pytest.raises(MentorError, match="step_reconciliation_required"):
            service.apply(resume)
        assert snapshot(service) == actual and len(generator.calls) == 1
    else:
        service.apply(resume)
        assert drain(system, conversation.id).status == "completed"
        assert len(generator.calls) == service.budgets.status(conversation)["total_requests"] == 4
        assert len(channel.sent) == 3
        assert len({delivery.artifact_id for delivery, _ in channel.sent}) == 3
