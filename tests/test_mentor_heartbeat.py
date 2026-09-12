"""Accelerated lease, wall-time and cleanup tests without model or network calls."""

from __future__ import annotations

from queue import Queue
import threading
from typing import Any

import pytest

from riji_agent.mentors import heartbeat
from riji_agent.mentors.models import Artifact, Command, Conversation, Delivery, MentorError
from riji_agent.mentors.planner import next_stage
from riji_agent.mentors.store import key
from riji_agent.models.types import LLMError
from test_mentor_discussions import prepare, system  # noqa: F401
from test_private_mentor_context import Sources, create_private


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.value

    def advance(self, seconds: float) -> float:
        with self.lock:
            self.value += seconds
            return self.value


@pytest.fixture
def timed(system: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Clock]:
    clock = Clock()
    service, worker = system[:2]
    for component in (service, service.policy, service.budgets, worker):
        component.now = clock
    monkeypatch.setattr(heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.002)
    return system, clock


def generated(service: Any, conversation: Conversation) -> tuple[Artifact, ...]:
    return tuple(item for item in service.store.list("artifact", conversation.id, Artifact)
                 if item.kind != "user")


def advance_with_renewals(worker: Any, clock: Clock, monkeypatch: pytest.MonkeyPatch):
    observations: Queue[float] = Queue()
    original = worker._renew

    def renew(request, completed=None):
        original(request, completed)
        current = worker.store.read("conversation", request.conversation.id, Conversation)
        observations.put(current.lease_until)

    monkeypatch.setattr(worker, "_renew", renew)

    def advance(seconds: float) -> None:
        expected = clock.advance(seconds) + heartbeat.LEASE_TTL_SECONDS
        while observations.get(timeout=2) < expected:
            pass

    return advance


def test_active_slow_generation_commits_once_without_another_charge(timed, monkeypatch):
    system, clock = timed
    service, worker, dispatcher, channel, generator = system[:5]
    conversation = prepare(system)
    advance = advance_with_renewals(worker, clock, monkeypatch)

    def slow(request):
        for _ in range(8):
            advance(30)
        assert worker.run_one(conversation.id) is False

    generator.after_send = slow
    assert worker.run_one(conversation.id)
    assert clock() == 1240
    assert len(generator.calls) == service.budgets.status(conversation)["total_requests"] == 1
    assert len(generated(service, conversation)) == 1
    assert len(service.store.list("delivery", conversation.id, Delivery)) == 1
    assert dispatcher.dispatch_one(conversation.id)
    assert not dispatcher.dispatch_one(conversation.id)
    assert len(channel.sent) == 1
    with service.store.transaction() as db:
        assert db.execute("SELECT status FROM mentor_steps").fetchone()[0] == "succeeded"
    assert heartbeat.LEASE_TTL_SECONDS == 180


@pytest.mark.parametrize("kind,time_limit", [("private", 120), ("roundtable", 600)])
def test_response_after_wall_budget_is_rejected_without_retry(timed, monkeypatch, kind, time_limit):
    system, clock = timed
    service, worker, dispatcher, channel, generator = system[:5]
    conversation = (create_private(system, "gentle_reviewer") if kind == "private" else prepare(system))
    advance = advance_with_renewals(worker, clock, monkeypatch)

    def slow(request):
        for _ in range((time_limit - 30) // 30):
            advance(30)
        clock.advance(31)

    generator.after_send = slow
    assert worker.run_one(conversation.id)
    assert generated(service, conversation) == ()
    assert not service.store.list("delivery", conversation.id, Delivery)
    assert not dispatcher.dispatch_one(conversation.id) and not channel.sent
    assert len(generator.calls) == service.budgets.status(conversation)["total_requests"] == 1
    with service.store.transaction() as db:
        assert service.store.lookup(db, "blocked", conversation.id) == "budget_exhausted"
        assert db.execute("SELECT time_limit FROM mentor_budgets").fetchone()[0] == time_limit


def claimed_request(system):
    service, worker = system[:2]
    conversation = prepare(system)
    current, execution = worker._claim(conversation.id)
    artifacts = service.store.list("artifact", conversation.id, Artifact)
    stage = next_stage(current, artifacts)
    return worker._request(current, execution, stage, artifacts), stage


def test_abandoned_execution_still_expires_and_cannot_be_revived(timed):
    system, clock = timed
    service, worker = system[:2]
    request, _ = claimed_request(system)
    original_until = request.conversation.lease_until
    clock.advance(180)
    with pytest.raises(MentorError, match="^lease_expired$"):
        worker._renew(request)
    assert service.store.read("conversation", request.conversation.id, Conversation).lease_until == original_until
    assert service.recover() == 1
    recovered = service.store.read("conversation", request.conversation.id, Conversation)
    assert recovered.status == "interrupted" and recovered.lease_until == 0
    assert service.budgets.status(recovered)["total_requests"] == 0


@pytest.mark.parametrize("change", ["owner_id", "run_id", "input_revision", "cancel_epoch", "lease_generation"])
def test_renewal_never_overwrites_replacement_execution(timed, change):
    system, _ = timed
    service, worker = system[:2]
    request, _ = claimed_request(system)
    with service.store.transaction() as db:
        current = service.store.get(db, "conversation", request.conversation.id, Conversation)
        value = getattr(current, change)
        replacement = current.model_copy(update={change: value + 1 if isinstance(value, int) else "replacement"})
        service.store.put(db, "conversation", replacement, replacement.owner_id)
    with pytest.raises(MentorError):
        worker._renew(request)
    assert service.store.read("conversation", replacement.id, Conversation) == replacement


def test_budget_is_checked_again_in_final_commit_transaction(timed, monkeypatch):
    system, clock = timed
    service, worker, _, _, generator = system[:5]
    conversation = create_private(system, "gentle_reviewer")
    original = worker._target

    def delayed_target(*args):
        result = original(*args)
        clock.advance(120)
        return result

    monkeypatch.setattr(worker, "_target", delayed_target)
    assert worker.run_one(conversation.id)
    assert len(generator.calls) == 1 and generated(service, conversation) == ()
    with service.store.transaction() as db:
        assert service.store.lookup(db, "blocked", conversation.id) == "budget_exhausted"


@pytest.mark.parametrize("action", ["stop", "correct", "worker_stop", "source", "audience"])
def test_revocation_during_call_stops_renewal_and_rejects_late_answer(timed, monkeypatch, action):
    system, clock = timed
    service, worker, _, channel, generator, principal = system[:6]
    source = Sources(principal, "gentle_reviewer")
    source.source = source.source.model_copy(update={"allowed_personas": ("gentle_reviewer", "blunt_coach")})
    service.policy.sources = source
    conversation = prepare(system)
    advance = advance_with_renewals(worker, clock, monkeypatch)

    def revoke(request):
        advance(30)
        if action == "worker_stop":
            worker.stop()
        elif action == "source":
            source.enabled = False
        elif action == "audience":
            channel.snapshot = channel.snapshot.model_copy(update={"human_subjects": ("stranger",)})
        else:
            service.apply(Command(id="change", principal_id=principal.id, conversation_id=conversation.id,
                kind="stop" if action == "stop" else "supplement", expected_revision=1,
                text="Correct the synthetic premise."))
        with pytest.raises(MentorError):
            worker._renew(request)

    generator.after_send = revoke
    assert worker.run_one(conversation.id)
    assert len(generator.calls) == 1 and not generated(service, conversation)
    assert not service.store.list("delivery", conversation.id, Delivery)


@pytest.mark.parametrize("failure", [False, True])
def test_heartbeat_thread_is_joined_on_success_and_exception(timed, monkeypatch, failure):
    system, _ = timed
    service, worker, _, _, generator = system[:5]
    conversation = prepare(system)
    instances = []
    original = heartbeat.ExecutionHeartbeat

    def capture(renew):
        instance = original(renew)
        instances.append(instance)
        return instance

    monkeypatch.setattr("riji_agent.mentors.worker.ExecutionHeartbeat", capture)
    if failure:
        def fail(request):
            raise LLMError("model_timeout")
        generator.after_send = fail
    assert worker.run_one(conversation.id)
    assert len(instances) == 1
    assert instances[0]._done.is_set() and not instances[0]._thread.is_alive()
    assert len(generator.calls) == 1
    assert len(generated(service, conversation)) == (0 if failure else 1)


def test_completed_context_cannot_renew_after_policy_check(timed, monkeypatch):
    system, clock = timed
    service, worker = system[:2]
    request, _ = claimed_request(system)
    completed = threading.Event()
    original = worker._generation_guard

    def complete_during_guard(request):
        result = original(request)
        completed.set()
        return result

    monkeypatch.setattr(worker, "_generation_guard", complete_during_guard)
    clock.advance(30)
    worker._renew(request, completed)
    assert service.store.read("conversation", request.conversation.id, Conversation).lease_until == 1180


def test_lease_cannot_be_revived_when_expiry_crosses_inside_renewal_transaction(timed, monkeypatch):
    system, clock = timed
    service, worker = system[:2]
    request, _ = claimed_request(system)
    clock.advance(179)
    original = service.budgets.check_time_in_transaction
    checks = []

    def cross_expiry(db, current):
        checks.append(current.id)
        if len(checks) == 2:
            clock.advance(2)
        original(db, current)

    monkeypatch.setattr(service.budgets, "check_time_in_transaction", cross_expiry)
    with pytest.raises(MentorError, match="^lease_expired$"):
        worker._renew(request)
    assert len(checks) == 2
    assert service.store.read("conversation", request.conversation.id, Conversation).lease_until == 1180


def test_heartbeat_failure_is_safe_and_does_not_retry_generation(timed, monkeypatch):
    system, _ = timed
    service, worker, _, _, generator = system[:5]
    conversation = prepare(system)
    failed = threading.Event()

    def fail_renew(*args):
        failed.set()
        raise ValueError("synthetic-private-error-must-not-be-persisted")

    monkeypatch.setattr(worker, "_renew", fail_renew)
    generator.after_send = lambda _: failed.wait(2)
    assert worker.run_one(conversation.id)
    assert failed.is_set() and len(generator.calls) == 1 and not generated(service, conversation)
    with service.store.transaction() as db:
        assert service.store.lookup(db, "blocked", conversation.id) == "execution_heartbeat_failed"
        assert db.execute("SELECT error FROM mentor_steps").fetchone()[0] == "execution_heartbeat_failed"


def test_stopped_worker_cannot_claim_another_generation(timed):
    system, _ = timed
    service, worker, _, _, generator = system[:5]
    conversation = prepare(system)
    worker.stop()
    assert not worker.run_one(conversation.id)
    assert worker._claim(conversation.id) is None
    assert generator.calls == []


def test_failure_cannot_rewrite_a_succeeded_step_even_same_generation(timed):
    system, _ = timed
    service, worker = system[:2]
    request, stage = claimed_request(system)
    step = key(request.execution.run_id, str(request.execution.input_revision), stage.kind,
               str(stage.round_index), stage.actor)
    worker._execute_claimed(request.conversation, request.execution)
    before = service.store.read("conversation", request.conversation.id, Conversation)
    worker._failed(request.execution, step, "lease_expired")
    assert service.store.read("conversation", before.id, Conversation) == before
    with service.store.transaction() as db:
        assert db.execute("SELECT status FROM mentor_steps WHERE key=?", (step,)).fetchone()[0] == "succeeded"


@pytest.mark.parametrize("uncertain", [False, True])
def test_corrected_input_records_only_old_receipt_not_new_revision_or_other_state(timed, uncertain):
    from test_mentor_heartbeat_races import snapshot
    system, _ = timed
    service, worker, _, _, generator, principal = system[:6]
    conversation = prepare(system, "reference")
    initial = service.store.list("artifact", conversation.id, Artifact)[0]
    expected = {}
    keys = {}

    def correct(request):
        service.apply(Command(id="correction", principal_id=principal.id, conversation_id=conversation.id,
            kind="correct", expected_revision=1, text="Use the corrected synthetic condition.",
            supersedes=(initial.id,)))
        current = service.get(conversation.id, principal.id)
        keys["new"] = key(current.run_id, str(current.input_revision), "opinion", "0", request.actor)
        keys["succeeded"] = key(current.run_id, str(request.execution.input_revision), "opinion", "0", "other")
        with service.store.transaction() as db:
            for name, status in (("new", "call_sent"), ("succeeded", "succeeded")):
                db.execute("INSERT INTO mentor_steps(key,conversation_id,status,attempted_at) VALUES (?,?,?,?)",
                           (keys[name], current.id, status, service.now()))
        expected.update(snapshot(service))
        if uncertain:
            raise LLMError("model_transport_failed")

    generator.after_send = correct
    assert worker.run_one(conversation.id)
    actual = snapshot(service)
    assert {k: v for k, v in actual.items() if k != "steps"} == {
        k: v for k, v in expected.items() if k != "steps"}
    unchanged = {keys["new"], keys["succeeded"]}
    assert [row for row in actual["steps"] if row[0] in unchanged] == [
        row for row in expected["steps"] if row[0] in unchanged]
    old = [row for row in actual["steps"] if row[0] not in unchanged]
    assert len(old) == 1 and old[0][2] == ("unknown" if uncertain else "failed")
    worker._failed(generator.calls[0].execution, keys["new"], "execution_stale")
    assert snapshot(service) == actual
    assert len(generator.calls) == service.budgets.status(conversation)["total_requests"] == 1
