"""Bounded single-step execution with leases, durable attempts and a local outbox."""

from __future__ import annotations

import threading
import time
from typing import Callable

from pydantic import ValidationError

from riji_agent.mentors.comparison import COMPARISON_ERRORS, validate_comparison
from riji_agent.mentors.heartbeat import ExecutionHeartbeat, LEASE_TTL_SECONDS
from riji_agent.mentors.models import (
    Artifact, ChatBinding, Conversation, Delivery, Execution,
    Generation, GenerationRequest, MentorError,
)
from riji_agent.mentors.planner import Stage, next_stage, visible_history
from riji_agent.mentors.ports import GenerationPort
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import key
from riji_agent.models.errors import UNCERTAIN_MODEL_OUTCOMES, model_failure_code
from riji_agent.models.types import LLMError


class DiscussionWorker:
    def __init__(self, service: DiscussionService, generator: GenerationPort,
                 now: Callable[[], float] = time.time) -> None:
        self.service, self.store, self.generator, self.now = service, service.store, generator, now
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_selected = ""
        self.execution_driver = None
        self.dispatcher = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.service.recover()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="riji-mentor-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.service.recover()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                progressed = self.run_one()
                if self.dispatcher is not None:
                    progressed = self.dispatcher.dispatch_one() or progressed
                from riji_agent.mentors.notices import dispatch_notice
                progressed = dispatch_notice(self.service) or progressed
            except Exception:
                progressed = False  # Domain errors are recorded without raw exception bodies.
            if not progressed:
                self._wake.wait(1)
                self._wake.clear()

    def run_one(self, conversation_id: str = "") -> bool:
        if self._stop.is_set():
            return False
        with self.store.transaction() as db:
            rows = db.execute("SELECT value FROM mentor_records WHERE kind='conversation'").fetchall()
        pending = [Conversation.model_validate_json(row[0]) for row in rows]
        for item in pending:
            if item.status == "provisioning" and (not conversation_id or item.id == conversation_id):
                try:
                    self.service.provision(item.id)
                except MentorError as exc:
                    self.service.policy.block(item.id, exc.code)
                return True
        claimed = self._claim(conversation_id)
        if claimed is None:
            return False
        conversation, execution = claimed
        if self.execution_driver is not None:
            return self.execution_driver(conversation, execution, self._execute_claimed)
        return self._execute_claimed(conversation, execution)

    def _execute_claimed(self, conversation: Conversation, execution: Execution) -> bool:
        artifacts = self.store.list("artifact", conversation.id, Artifact)
        stage = next_stage(conversation, artifacts)
        if stage is None:
            self._finish(execution)
            return True
        step = key(conversation.run_id, str(conversation.input_revision), stage.kind, str(stage.round_index), stage.actor)
        try:
            if not self._begin_step(conversation, step):
                self._finish(execution)
                return True
            self.service.policy.check(execution)
            request = self._request(conversation, execution, stage, artifacts)
            result = self._generate(request)
            self._generation_guard(request)
            self._commit(execution, stage, step, result, request)
        except MentorError as exc:
            self._failed(execution, step, exc.code)
        except LLMError as exc:
            self._failed(execution, step, model_failure_code(exc))
        except (ValidationError, ValueError):
            self._failed(execution, step, "invalid_generation")
        except Exception:
            self._failed(execution, step, "generation_unknown")
        return True

    def _generate(self, request: GenerationRequest) -> Generation:
        repairable = {"unsupported_source", "invalid_response_target", "debate_response_required",
                      "independent_opinion_required", "unexpected_tool_request"} | COMPARISON_ERRORS
        for attempt in range(2):
            final = request.stage == "synthesis" or (request.stage == "comparison" and request.conversation.mode == "reference")
            self.service.budgets.charge(request.conversation, request.background, final=final)
            try:
                with ExecutionHeartbeat(lambda done: self._renew(request, done)) as heartbeat:
                    def guard() -> None:
                        heartbeat.check()
                        self._generation_guard(request)
                    result = self.generator.generate(request, guard)
                self._generation_guard(request)
                self._validate_result(result, request)
                return result
            except (ValidationError, ValueError):
                hint = "invalid_generation"
            except MentorError as exc:
                if exc.code not in repairable:
                    raise
                hint = exc.code
            if attempt == 1:
                raise MentorError("invalid_generation")
            request = request.model_copy(update={"repair_hint": hint})
        raise MentorError("invalid_generation")

    def _generation_guard(self, request: GenerationRequest) -> Conversation:
        if self._stop.is_set():
            raise MentorError("execution_inactive")
        current = self.service.policy.check(request.execution)
        if current.owner_id != request.conversation.owner_id:
            raise MentorError("execution_stale")
        self.service.budgets.check_time(current)
        return current

    def _renew(self, request: GenerationRequest, completed: threading.Event | None = None) -> None:
        if completed is not None and completed.is_set():
            return
        self._generation_guard(request)
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", request.execution.conversation_id, Conversation)
            self.service.policy._check_execution(current, request.execution, True)
            if current.owner_id != request.conversation.owner_id or current.status != "running":
                raise MentorError("execution_stale")
            self.service.budgets.check_time_in_transaction(db, current)
            if completed is not None and completed.is_set():
                return
            if self._stop.is_set():
                raise MentorError("execution_inactive")
            renewed_at = self.now()
            if current.lease_until <= renewed_at:
                raise MentorError("lease_expired")
            updated = current.model_copy(update={"lease_until": renewed_at + LEASE_TTL_SECONDS})
            self.store.put(db, "conversation", updated, updated.owner_id)

    def _claim(self, identifier: str) -> tuple[Conversation, Execution] | None:
        with self.store.transaction() as db:
            if self._stop.is_set():
                return None
            rows = db.execute("SELECT value FROM mentor_records WHERE kind='conversation' ORDER BY id").fetchall()
            candidates = [Conversation.model_validate_json(row[0]) for row in rows]
            candidates = [item for item in candidates if item.status == "queued" and item.lease_until <= self.now()
                          and (not identifier or identifier == item.id)]
            if not candidates:
                return None
            selected = next((item for item in candidates if item.id > self._last_selected), candidates[0])
            self._last_selected = selected.id
            current = selected.model_copy(update={"status": "running", "lease_generation": selected.lease_generation + 1,
                                                  "lease_until": self.now() + LEASE_TTL_SECONDS, "updated_at": self.now()})
            self.store.put(db, "conversation", current, current.owner_id)
            execution = Execution(conversation_id=current.id, owner_id=current.owner_id, run_id=current.run_id,
                                  input_revision=current.input_revision, cancel_epoch=current.cancel_epoch,
                                  lease_generation=current.lease_generation)
            return current, execution

    def _begin_step(self, conversation: Conversation, step: str) -> bool:
        with self.store.transaction() as db:
            previous = db.execute("SELECT status FROM mentor_steps WHERE key=?", (step,)).fetchone()
            if previous:
                if previous[0] == "succeeded":
                    return False
                raise MentorError("step_reconciliation_required")
            db.execute("INSERT INTO mentor_steps(key,conversation_id,status,attempted_at) VALUES (?,?,?,?)",
                       (step, conversation.id, "call_sent", self.now()))
            return True

    def _request(self, conversation: Conversation, execution: Execution, stage: Stage,
                 artifacts: tuple[Artifact, ...]) -> GenerationRequest:
        current = conversation
        if current.source_scope == "group_only":
            self.service.policy.group_content(current)
        summary = self.service.summaries.current(current, reanalyze=current.reanalyze)
        if current.run_kind == "followup":
            with self.store.transaction() as db:
                question = self.store.lookup(db, "followup_question", current.run_id)
            current = current.model_copy(update={"question": question or current.question})
        elif summary is not None:
            # The original topic may contain a corrected premise. The effective
            # user context, rather than that immutable archive title, is sent.
            current = current.model_copy(update={"question": "\n".join(
                item.text for item in summary.items if item.kind.startswith("user_"))})
        background = self.service.policy.background(conversation)
        if current.reanalyze:
            background = tuple(source for source in background if source.kind != "shared_excerpt"
                               or source.content_kind in {"user_statement", "user_plan", "user_feedback"})
        if conversation.kind == "private":
            from riji_agent.mentors.private_context import private_history
            previous = private_history(self.service, conversation, artifacts)
        else:
            previous = visible_history(stage, artifacts, conversation)
        return GenerationRequest(conversation=current, actor=stage.actor, stage=stage.kind,
                                 round_index=stage.round_index, background=background,
                                 previous=previous, execution=execution, working_summary=summary)

    @staticmethod
    def _validate_result(result: Generation, request: GenerationRequest) -> None:
        validate_comparison(result, request)
        allowed = {source.id for source in request.background}
        allowed.update(ref for artifact in request.previous for ref in artifact.source_refs)
        allowed.update(artifact.id for artifact in request.previous)
        if request.working_summary is not None:
            allowed.update(ref for item in request.working_summary.items for ref in item.artifact_ids)
            allowed.update(ref for item in request.working_summary.items for ref in item.source_refs)
        if not set(result.source_refs).issubset(allowed):
            raise MentorError("unsupported_source")
        previous_ids = {artifact.id for artifact in request.previous}
        if request.stage == "debate":
            previous_ids = {artifact.id for artifact in request.previous if artifact.actor != request.actor
                            and artifact.kind in {"opinion", "debate"}}
        if not set(result.responds_to).issubset(previous_ids):
            raise MentorError("invalid_response_target")
        if request.stage == "debate" and not result.responds_to:
            raise MentorError("debate_response_required")
        if request.stage == "opinion" and result.responds_to:
            raise MentorError("independent_opinion_required")

    def _commit(self, execution: Execution, stage: Stage, step: str,
                result: Generation, request: GenerationRequest) -> None:
        dependencies = set(request.conversation.source_ids)
        dependencies.update(dependency for item in request.previous for dependency in item.dependencies)
        if request.working_summary is not None:
            dependencies.update(dependency for item in request.working_summary.items for dependency in item.dependencies)
        artifact = Artifact(conversation_id=execution.conversation_id, actor=stage.actor, kind=stage.kind,
                            input_revision=execution.input_revision, run_id=execution.run_id,
                            round_index=stage.round_index,
                            origin_room_id=request.conversation.room_id if request.conversation.source_scope == "group_only" else "",
                            created_at=self.now(), dependencies=tuple(sorted(dependencies)), **result.model_dump())
        target = self._target(request.conversation, artifact.actor)
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", execution.conversation_id, Conversation)
            self.service.policy._check_execution(current, execution, True)
            if self._stop.is_set():
                raise MentorError("execution_inactive")
            self.service.budgets.check_time_in_transaction(db, current)
            self.store.put(db, "artifact", artifact, current.id)
            self._outbox(db, current, artifact, target)
            db.execute("UPDATE mentor_steps SET status='succeeded',artifact_id=? WHERE key=?", (artifact.id, step))
            completed = current.kind == "private" or current.run_kind == "followup"
            current = current.model_copy(update={"status": "delivering" if completed else "queued", "lease_until": 0,
                                                  "state_revision": current.state_revision + 1, "updated_at": self.now()})
            if completed:
                current = self.service.summaries.refresh(db, current)
            self.store.put(db, "conversation", current, current.owner_id)
            self.service.summaries.record_run(db, current)

    def _target(self, conversation: Conversation, actor: str) -> tuple[str, str]:
        if conversation.kind == "private":
            with self.store.transaction() as db:
                origin = self.store.lookup(db, "origin", conversation.id)
                binding = self.store.get(db, "chat", origin, ChatBinding)
            return binding.application_id, binding.external_chat_id
        apps = self.service.applications(conversation)
        return next(app.id for app in apps if app.persona_id == actor), conversation.room_id

    def _outbox(self, db, conversation: Conversation, artifact: Artifact, target: tuple[str, str]) -> None:
        existing = self.store.records(db, "delivery", conversation.id, Delivery)
        sequence = max((item.sequence for item in existing), default=0) + 1
        application_id, chat_id = target
        delivery = Delivery(conversation_id=conversation.id, sequence=sequence,
                            application_id=application_id, chat_id=chat_id, artifact_id=artifact.id,
                            input_revision=conversation.input_revision, cancel_epoch=conversation.cancel_epoch)
        self.store.put(db, "delivery", delivery, conversation.id)

    def _finish(self, execution: Execution) -> None:
        with self.store.transaction() as db:
            conversation = self.store.get(db, "conversation", execution.conversation_id, Conversation)
            self.service.policy._check_execution(conversation, execution, True)
            updated = conversation.model_copy(update={"status": "delivering", "lease_until": 0})
            updated = self.service.summaries.refresh(db, updated)
            self.store.put(db, "conversation", updated, updated.owner_id)
            self.service.summaries.record_run(db, updated)

    def _failed(self, execution: Execution, step: str, code: str) -> None:
        with self.store.transaction() as db:
            conversation = self.store.get(db, "conversation", execution.conversation_id, Conversation)
            if conversation is None or conversation.status == "deleted":
                return
            failure_status = ("unknown" if code in UNCERTAIN_MODEL_OUTCOMES or code == "generation_unknown"
                              else "failed")
            if not self.service.policy.same_execution(conversation, execution):
                # A stopped, already-returned attempt is known locally. Record
                # only its receipt so explicit resume need not reconcile it;
                # never alter a replacement owner's state or successful step.
                if self._cancelled_receipt_allowed(conversation, execution, step):
                    db.execute("UPDATE mentor_steps SET status=?,error=? WHERE key=? "
                               "AND conversation_id=? AND status='call_sent'",
                               (failure_status, code, step, execution.conversation_id))
                return
            previous = db.execute("SELECT status FROM mentor_steps WHERE key=?", (step,)).fetchone()
            if previous is not None and previous[0] == "succeeded":
                return
            db.execute("UPDATE mentor_steps SET status=?,error=? WHERE key=?", (failure_status, code, step))
            status = "partial" if code in {"budget_exhausted", "source_budget_exhausted"} else "interrupted"
            if (code == "budget_exhausted" and conversation.kind == "roundtable"
                    and conversation.run_kind != "followup" and not conversation.summarize_requested):
                status = "queued"
                conversation = conversation.model_copy(update={"summarize_requested": True})
            updated = conversation.model_copy(update={"status": status, "lease_until": 0, "updated_at": self.now()})
            self.store.put(db, "conversation", updated, updated.owner_id)
            self.service.summaries.record_run(db, updated)
            self.store.bind(db, "blocked", updated.id, code)
            self.store.bind(db, "private_notice", updated.id, code)
            if status != "queued":
                self.service.budgets.pause(db, updated)

    @staticmethod
    def _cancelled_receipt_allowed(conversation: Conversation, execution: Execution, step: str) -> bool:
        return (conversation.status in {"stopped", "interrupted", "waiting_user", "archived"}
                and conversation.lease_until == 0 and conversation.cancel_epoch > execution.cancel_epoch
                and (not execution.owner_id or conversation.owner_id == execution.owner_id)
                and conversation.input_revision >= execution.input_revision
                and step.startswith(key(execution.run_id, str(execution.input_revision))[:-1] + ",")
                and (conversation.run_id, conversation.lease_generation) == (
                    execution.run_id, execution.lease_generation))
