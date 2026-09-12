"""Commands for one persistent problem and independently bounded discussion runs."""

from __future__ import annotations

from riji_agent.mentors.models import AudienceGrant, Command, Conversation, Delivery, MentorError, new_id

ACTIVE = {"queued", "running", "delivering", "waiting_user", "interrupted"}
READY = {"completed", "partial", "stopped"}


class ProblemLifecycle:
    def __init__(self, service) -> None:
        self.service, self.store = service, service.store

    def apply(self, db, current: Conversation, command: Command) -> Conversation:
        if current.kind != "roundtable":
            raise MentorError("roundtable_required")
        if command.kind == "archive":
            stopped = self.service._stop(db, current, delete=False)
            return stopped.model_copy(update={"status": "archived"})
        if command.kind == "restore":
            if current.status != "archived":
                raise MentorError("problem_not_archived")
            self._room(current)
            return current.model_copy(update={"status": "stopped"})
        if current.status == "archived":
            raise MentorError("problem_archived")
        self._room(current)
        if command.kind == "start_run":
            return self.start(db, current, command)
        if command.kind == "correct":
            return self.correct(db, current, command)
        if command.kind in {"continue", "reanalyze"}:
            if current.status not in READY:
                raise MentorError("discussion_still_running")
            text = command.text or ("根据当前有效背景重新分析，不沿用旧 AI 建议。" if command.kind == "reanalyze"
                                    else "请接着当前问题与最新进展继续沟通。")
            current = current.model_copy(update={"reanalyze": command.kind == "reanalyze"})
            return self.followup(db, current, command.model_copy(update={"text": text}), record_user=False)
        raise MentorError("unknown_command")

    def start(self, db, current: Conversation, command: Command) -> Conversation:
        if current.suspended_run_id:
            raise MentorError("discussion_resume_required")
        if current.status not in READY:
            raise MentorError("discussion_still_running")
        self._reconciled(db, current)
        actors = command.personas or current.run_personas or current.personas
        if not 2 <= len(set(actors)) == len(actors) <= 4 or not set(actors).issubset(current.personas):
            raise MentorError("invalid_roundtable")
        current = self.service.summaries.prepare(db, current)
        self.service.summaries.record_run(db, current)
        updated = current.model_copy(update={
            "run_id": new_id(), "run_number": current.run_number + 1, "run_kind": "roundtable",
            "run_personas": actors, "mode": command.mode, "rounds": command.rounds,
            "debate_started": command.mode == "debate", "input_revision": current.input_revision + 1,
            "cancel_epoch": current.cancel_epoch + 1, "status": "queued", "room_status": "ready",
            "lease_until": 0, "summarize_requested": False, "followup_actor": "",
            "reanalyze": command.reanalyze,
        })
        if command.text.strip():
            self.service._user_artifact(db, updated, command.text, statement_kind=command.statement_kind)
            updated = self.service.summaries.refresh(db, updated)
            if updated.summary_status != "current":
                updated = updated.model_copy(update={"status": "waiting_user"})
        self._advance_grant(db, updated)
        self._budget(db, updated)
        return updated

    def followup(self, db, current: Conversation, command: Command, *, record_user: bool = True,
                 interrupt: bool = False) -> Conversation:
        actor = command.actor or "host"
        if actor not in (*current.personas, "host"):
            raise MentorError("actor_not_allowed")
        if not interrupt:
            self._reconciled(db, current)
        self.service.summaries.record_run(db, current)
        updated = current.model_copy(update={
            "run_id": new_id(), "run_kind": "followup", "followup_actor": actor,
            "input_revision": current.input_revision + 1, "cancel_epoch": current.cancel_epoch + 1,
            "status": "queued", "room_status": "ready", "lease_until": 0,
            "summarize_requested": False,
        })
        self.store.bind(db, "followup_question", updated.run_id, command.text)
        if record_user:
            self.service._user_artifact(db, updated, command.text, statement_kind=command.statement_kind)
        updated = self.service.summaries.refresh(db, updated)
        if updated.summary_status != "current":
            updated = updated.model_copy(update={"status": "waiting_user"})
        self._advance_grant(db, updated)
        self._budget(db, updated)
        return updated

    def supplement(self, db, current: Conversation, command: Command) -> Conversation:
        self._room(current)
        if current.status == "archived":
            raise MentorError("problem_archived")
        if not command.text.strip():
            raise MentorError("question_required")
        if current.status in READY:
            return self.followup(db, current, command)
        if command.kind == "followup":
            return self.interrupt(db, current, command)
        if current.status not in {"queued", "running", "waiting_user", "delivering", "interrupted"}:
            raise MentorError("discussion_still_running")
        self.service.budgets.pause(db, current)
        self.service.policy._cancel_pending(db, current.id)
        updated = current.model_copy(update={"input_revision": current.input_revision + 1,
            "cancel_epoch": current.cancel_epoch + 1, "lease_until": 0, "status": "waiting_user"})
        if updated.run_kind == "followup":
            self.store.bind(db, "followup_question", updated.run_id, command.text)
        self.service._user_artifact(db, updated, command.text, statement_kind=command.statement_kind)
        updated = self.service.summaries.refresh(db, updated)
        self._advance_grant(db, updated)
        return updated

    def interrupt(self, db, current: Conversation, command: Command) -> Conversation:
        if current.run_kind == "followup" or current.suspended_run_id:
            raise MentorError("discussion_still_running")
        if current.status not in {"queued", "running", "waiting_user", "delivering"}:
            raise MentorError("discussion_still_running")
        self.service.budgets.pause(db, current)
        self.service.policy._cancel_pending(db, current.id)
        suspended = current.model_copy(update={"status": "waiting_user", "lease_until": 0})
        self.store.put(db, "suspended_run", suspended, current.id)
        current = suspended.model_copy(update={"suspended_run_id": current.run_id})
        return self.followup(db, current, command, interrupt=True)

    def resume(self, db, current: Conversation) -> Conversation:
        if current.status not in {"completed", "partial", "stopped", "waiting_user"}:
            raise MentorError("discussion_still_running")
        self._room(current)
        self._reconciled(db, current)
        suspended = self.store.get(db, "suspended_run", current.id, Conversation)
        if suspended is None or suspended.run_id != current.suspended_run_id:
            raise MentorError("suspended_run_unavailable")
        self.service.summaries.record_run(db, current)
        updated = current.model_copy(update={
            "run_id": suspended.run_id, "run_kind": suspended.run_kind,
            "run_personas": suspended.run_personas, "mode": suspended.mode, "rounds": suspended.rounds,
            "debate_started": suspended.debate_started, "reanalyze": suspended.reanalyze,
            "followup_actor": "", "suspended_run_id": "", "status": "queued", "lease_until": 0,
            "input_revision": current.input_revision + 1, "cancel_epoch": current.cancel_epoch + 1,
            "summarize_requested": suspended.summarize_requested,
        })
        updated = self.service.summaries.prepare(db, updated)
        db.execute("DELETE FROM mentor_records WHERE kind='suspended_run' AND id=?", (current.id,))
        self._advance_grant(db, updated)
        self.service.budgets.activate(db, updated)
        return updated

    def correct(self, db, current: Conversation, command: Command) -> Conversation:
        if not command.text.strip():
            raise MentorError("question_required")
        if not command.supersedes and not command.replace_background:
            raise MentorError("correction_target_required")
        self.service.summaries.correct(db, current, command.supersedes)
        current = current.model_copy(update={"correction_version": current.correction_version + 1})
        return self.supplement(db, current, command)

    @staticmethod
    def _room(current: Conversation) -> None:
        if current.room_status not in {"ready", "ended"}:
            raise MentorError("room_reverification_required")

    def _budget(self, db, current: Conversation) -> None:
        if current.status == "waiting_user":
            self.service.budgets.create(db, current)
        else:
            self.service.budgets.activate(db, current)

    def _reconciled(self, db, current: Conversation) -> None:
        rows = db.execute("SELECT 1 FROM mentor_steps WHERE conversation_id=? AND status IN ('unknown','call_sent')",
                          (current.id,)).fetchone()
        deliveries = self.store.records(db, "delivery", current.id, Delivery)
        if rows or any(item.status in {"unknown", "sending", "failed"} for item in deliveries):
            raise MentorError("step_reconciliation_required")

    def _advance_grant(self, db, current: Conversation) -> None:
        if current.grant_id:
            grant = self.store.get(db, "grant", current.grant_id, AudienceGrant)
            self.store.put(db, "grant", grant.model_copy(update={"input_revision": current.input_revision}), current.owner_id)
