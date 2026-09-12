"""Versioned extractive problem context with explicit provenance and bounded size.

This module never infers that quoted speech, an aspiration or an AI recommendation
is a user fact. User text remains an attributed statement unless its input kind
was explicitly supplied. No model calls or long-term-memory captures occur here.
"""

from __future__ import annotations

from riji_agent.mentors.models import (
    Artifact, Conversation, DiscussionRun, MentorError, Principal, Source,
    SummaryItem, WorkingSummary,
)

MAX_CONTEXT_CHARS = 18000
MAX_CONTEXT_ITEMS = 48


class ProblemSummaries:
    def __init__(self, service) -> None:
        self.service, self.store = service, service.store

    def record_run(self, db, conversation: Conversation) -> None:
        previous = self.store.get(db, "run", conversation.run_id, DiscussionRun)
        record = DiscussionRun(
            id=conversation.run_id, conversation_id=conversation.id,
            number=conversation.run_number, kind=conversation.run_kind,
            mode=conversation.mode,
            personas=(conversation.followup_actor or "host",) if conversation.run_kind == "followup"
            else conversation.run_personas or conversation.personas,
            summary_version=previous.summary_version if previous else conversation.summary_version,
            input_revision=conversation.input_revision, status=conversation.status,
            reanalyze=conversation.reanalyze,
            created_at=previous.created_at if previous else self.service.now(),
            updated_at=self.service.now(),
        )
        self.store.put(db, "run", record, conversation.id)

    def superseded(self, db, conversation: Conversation) -> set[str]:
        rows = db.execute("SELECT key FROM mentor_keys WHERE kind='superseded' AND value=?",
                          (conversation.id,)).fetchall()
        return {row[0] for row in rows}

    def correct(self, db, conversation: Conversation, identifiers: tuple[str, ...]) -> None:
        artifacts = self.store.records(db, "artifact", conversation.id, Artifact)
        user_ids = {item.id for item in artifacts if item.kind == "user"}
        if identifiers and not set(identifiers).issubset(user_ids):
            raise MentorError("correction_target_invalid")
        replaced = set(identifiers) if identifiers else user_ids
        # AI conclusions are dependent on the former shared context. Keep the
        # archive but conservatively invalidate all of them as current premises.
        replaced.update(item.id for item in artifacts if item.kind != "user")
        for identifier in replaced:
            self.store.bind(db, "superseded", identifier, conversation.id)

    def refresh(self, db, conversation: Conversation) -> Conversation:
        if conversation.kind != "roundtable":
            return conversation
        artifacts = self.store.records(db, "artifact", conversation.id, Artifact)
        invalid = self.superseded(db, conversation)
        active = tuple(item for item in artifacts if item.id not in invalid)
        users = [self._item(item) for item in active if item.kind == "user"]
        conclusions = [item for item in active if item.kind in {"synthesis", "comparison", "followup"}]
        advice = [] if conversation.reanalyze or not conclusions else self._advice_items(conclusions[-1])
        items = tuple(users + advice)
        over = len(items) > MAX_CONTEXT_ITEMS or sum(len(item.text) for item in items) > MAX_CONTEXT_CHARS
        previous = self.store.get(db, "summary", conversation.summary_id, WorkingSummary)
        covered = tuple(item.id for item in active)
        if previous and previous.covered_artifact_ids == covered and previous.items == items:
            return conversation
        summary = WorkingSummary(
            conversation_id=conversation.id, version=conversation.summary_version + 1,
            input_revision=conversation.input_revision, correction_version=conversation.correction_version,
            covered_artifact_ids=covered, items=() if over else items,
            status="pending" if over else "current",
            pending_artifact_ids=tuple(item.id for item in active) if over else (),
            created_at=self.service.now(),
        )
        self.store.put(db, "summary", summary, conversation.id)
        return conversation.model_copy(update={"summary_id": summary.id, "summary_version": summary.version,
                                               "summary_status": summary.status})

    @staticmethod
    def _item(artifact: Artifact) -> SummaryItem:
        user = artifact.kind == "user"
        kind = artifact.origin_kind if user and artifact.origin_kind != "ai_discussion" else "user_statement" if user else "ai_advice"
        return SummaryItem(kind=kind, text=artifact.text, artifact_ids=(artifact.id,),
                           source_refs=artifact.source_refs, dependencies=artifact.dependencies,
                           occurred_at=artifact.created_at)

    def _advice_items(self, artifact: Artifact) -> list[SummaryItem]:
        base = self._item(artifact)
        items = [base]
        items.extend(base.model_copy(update={"kind": "unresolved", "text": text})
                     for text in artifact.uncertainties)
        items.extend(base.model_copy(update={"text": text}) for text in artifact.next_steps)
        return items

    def current(self, conversation: Conversation, *, reanalyze: bool = False) -> WorkingSummary | None:
        if conversation.kind != "roundtable":
            return None
        summary = self.store.read("summary", conversation.summary_id, WorkingSummary)
        if summary is None or summary.status != "current" or summary.correction_version != conversation.correction_version:
            raise MentorError("summary_refresh_required")
        principal = self.store.read("principal", conversation.owner_id, Principal)
        for item in summary.items:
            for identifier in item.dependencies:
                source = self.store.read("source", identifier, Source)
                if source is None or not self.service.policy._usable(source, principal, conversation):
                    raise MentorError("source_revoked")
        if reanalyze:
            summary = summary.model_copy(update={"items": tuple(item for item in summary.items
                                                               if item.kind not in {"ai_advice", "unresolved"})})
        return summary

    def prepare(self, db, conversation: Conversation) -> Conversation:
        updated = self.refresh(db, conversation)
        if updated.summary_status != "current":
            raise MentorError("summary_refresh_required")
        return updated
