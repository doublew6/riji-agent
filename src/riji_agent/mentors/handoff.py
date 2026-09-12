"""Versioned AI results become journal drafts only in a verified host private chat."""

from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import replace
from difflib import unified_diff

from pydantic import Field

from riji_agent.drafts.confirmation import ConfirmationContext, PrivatePreviewScope, preview_hash
from riji_agent.drafts.models import DraftOperation, DraftStatus
from riji_agent.journal.content import AI_RESULT, DiscussionProvenance
from riji_agent.mentors.history import DiscussionHistory, digest
from riji_agent.mentors.models import Application, Artifact, ChatBinding, Conversation, MentorError, Principal, Record, Source, new_id
from riji_agent.mentors.store import key


class Handoff(Record):
    id: str = Field(default_factory=new_id)
    conversation_id: str
    principal_id: str
    artifact_ids: tuple[str, ...]
    text: str
    binding_id: str = ""
    draft_id: str = ""
    provenance: DiscussionProvenance | None = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    target_date: date | None = None
    superseded_by: str = ""
    previous_handoff_id: str = ""
    revision_note: str = ""
    revision_request_hash: str = ""


class JournalHandoff:
    def __init__(self, history: DiscussionHistory, drafts) -> None:
        self.history, self.store, self.drafts = history, history.store, drafts
        history.drafts = drafts

    def create(self, conversation_id: str, principal_id: str, artifact_ids: tuple[str, ...], *,
               operation_id: str = "") -> Handoff:
        view = self.history.read(conversation_id, principal_id)
        chosen = [item for item in view["artifacts"] if item["id"] in artifact_ids]
        if (not 1 <= len(artifact_ids) <= 5 or len(chosen) != len(artifact_ids)
                or len(set(artifact_ids)) != len(artifact_ids)
                or any("unavailable" in item or item.get("superseded") for item in chosen)):
            raise MentorError("handoff_selection_invalid")
        handoff = _selected_handoff(view, chosen, principal_id)
        with self.store.transaction() as db:
            operation = key(principal_id, conversation_id, operation_id) if operation_id else ""
            existing_id = self.store.lookup(db, "journal_handoff_operation", operation) if operation else None
            if existing_id:
                existing = self.store.get(db, "handoff", existing_id, Handoff)
                if existing is None or set(existing.artifact_ids) != set(artifact_ids):
                    raise MentorError("handoff_operation_conflict")
                return existing
            self.store.put(db, "handoff", handoff, conversation_id)
            if operation:
                self.store.bind(db, "journal_handoff_operation", operation, handoff.id)
        return handoff

    def _check(self, identifier: str, binding: ChatBinding) -> Handoff:
        handoff = self.store.read("handoff", identifier, Handoff)
        app = self.store.read("application", binding.application_id, Application)
        registered = self.store.read("chat", binding.id, ChatBinding)
        if (handoff is None or handoff.principal_id != binding.principal_id or binding.chat_type != "p2p"
                or app is None or app.role != "host" or registered != binding
                or (handoff.binding_id and handoff.binding_id != binding.id)):
            raise MentorError("verified_riji_private_chat_required")
        if handoff.superseded_by:
            raise MentorError("handoff_preview_superseded")
        view = self.history.read(handoff.conversation_id, binding.principal_id)
        valid = {item["id"]: item for item in view["artifacts"]
                 if "unavailable" not in item and not item.get("superseded")}
        if (handoff.provenance is None
                or handoff.provenance.correction_version != view["conversation"].get("correction_version", 0)
                or any(identifier not in valid or _artifact_hash(valid[identifier]) != expected
                       for identifier, expected in handoff.artifact_hashes.items())
                or set(handoff.artifact_ids) != set(handoff.artifact_hashes)):
            self._invalidate(handoff)
            raise MentorError("handoff_source_unavailable")
        return handoff

    def _invalidate(self, handoff: Handoff) -> None:
        principal = self.store.read("principal", handoff.principal_id, Principal)
        if self.drafts is not None and handoff.draft_id and principal is not None:
            self.drafts.purge_uncommitted_draft(handoff.draft_id, user_id=principal.legacy_owner_key)

    def preview(self, identifier: str, binding: ChatBinding, event_id: str) -> str:
        handoff = self._check(identifier, binding)
        principal = self.store.read("principal", binding.principal_id, Principal)
        if self.drafts is None or principal is None:
            raise MentorError("draft_service_unavailable")
        # The mentor transaction serializes simultaneous delivery retries before a draft exists.
        with self.store.transaction() as db:
            handoff = self.store.get(db, "handoff", identifier, Handoff)
            if handoff.draft_id:
                draft = self.drafts.get_draft(handoff.draft_id)
            else:
                preview = self.drafts.create_draft(user_id=principal.legacy_owner_key,
                    session_id=principal.legacy_owner_key + ":gentle_reviewer:" + handoff.id,
                    persona_id="gentle_reviewer", operations=(DraftOperation(
                        "🧠 Notes", handoff.text, AI_RESULT, handoff.provenance),), target_date=handoff.target_date)
                draft = self.drafts.get_draft(preview.draft_id)
                handoff = handoff.model_copy(update={"binding_id": binding.id, "draft_id": draft.draft_id})
                self.store.put(db, "handoff", handoff, handoff.conversation_id)
        self.drafts.bind_preview(draft.draft_id, scope=self._scope(handoff, binding, principal), display_event_id=event_id)
        instructions = ("\n删改内容：/修改转交 " + handoff.id + " | 改稿正文"
                        + "\n修改日期：/转交日期 " + handoff.id + " YYYY-MM-DD")
        return handoff.revision_note + self.drafts.render_preview(draft) + instructions + "\n\n确认请发送：/确认转交 " + handoff.id

    def revise(self, identifier: str, binding: ChatBinding, event_id: str, *,
               text: str | None = None, target_date: date | None = None) -> str:
        if not event_id or (text is None and target_date is None):
            raise MentorError("handoff_revision_required")
        if text is not None and not 1 <= len(text.strip()) <= 6000:
            raise MentorError("handoff_selection_too_large")
        if target_date is not None and type(target_date) is not date:
            raise MentorError("handoff_target_date_invalid")
        request_hash = digest({"text": text, "target_date": target_date.isoformat() if target_date else None})
        operation = key(binding.principal_id, identifier, event_id)
        with self.store.transaction() as db:
            saved = self.store.lookup(db, "handoff_revision", operation)
        if saved:
            previous = self._check(saved, binding)
            if previous.revision_request_hash != request_hash:
                raise MentorError("handoff_operation_conflict")
            return self.preview(saved, binding, event_id)
        old = self._check(identifier, binding)
        if self.drafts is None:
            raise MentorError("draft_service_unavailable")
        draft = self.drafts.get_draft(old.draft_id) if old.draft_id else None
        revised = _revision(old, text, target_date, draft).model_copy(update={"revision_request_hash": request_hash})
        with self.store.transaction() as db:
            current = self.store.get(db, "handoff", old.id, Handoff)
            if current != old:
                raise MentorError("handoff_preview_superseded")
            if draft and draft.status == DraftStatus.COMMITTING:
                raise MentorError("handoff_commit_in_progress")
            if draft and draft.status != DraftStatus.COMMITTED:
                self.drafts.cancel_draft(draft.draft_id, user_id=draft.user_id)
            self.store.put(db, "handoff", old.model_copy(update={"superseded_by": revised.id}), old.conversation_id)
            self.store.put(db, "handoff", revised, revised.conversation_id)
            self.store.bind(db, "handoff_revision", operation, revised.id)
        return self.preview(revised.id, binding, event_id)

    def confirm(self, identifier: str, binding: ChatBinding, event_id: str) -> str:
        handoff = self._check(identifier, binding)
        if not handoff.draft_id:
            raise MentorError("private_preview_required")
        principal = self.store.read("principal", binding.principal_id, Principal)
        draft = self.drafts.get_draft(handoff.draft_id)
        if draft.status == DraftStatus.COMMITTED:
            return "已按确认内容保存日记，本次未重复追加。来源：" + draft.source_id
        confirmation = ConfirmationContext(scope=self._scope(handoff, binding, principal),
            draft_id=draft.draft_id, preview_hash=preview_hash(draft), event_id=event_id, token=draft.token)
        with self.store.transaction() as db:
            current = self.store.get(db, "conversation", handoff.conversation_id, Conversation)
            if (current is None or current.status == "deleted"
                    or getattr(current, "correction_version", 0) != handoff.provenance.correction_version
                    or self.store.get(db, "handoff", handoff.id, Handoff) != handoff):
                raise MentorError("handoff_source_unavailable")
            guard = lambda: self._validate_commit_sources(db, handoff, principal)
            guard()
            result = self.drafts.commit_draft(draft.draft_id, user_id=principal.legacy_owner_key,
                                              token=draft.token, confirmation=confirmation, before_write=guard)
        return "已按确认内容保存日记。来源：" + result.source_id

    def _validate_commit_sources(self, db, handoff: Handoff, principal: Principal) -> None:
        current = self.store.get(db, "conversation", handoff.conversation_id, Conversation)
        if (current is None or current.status == "deleted"
                or current.correction_version != handoff.provenance.correction_version):
            raise MentorError("handoff_source_unavailable")
        policy = self.history.service.policy.sources
        transactional = getattr(policy, "validate_in_transaction", None)
        for identifier, expected in handoff.artifact_hashes.items():
            artifact = self.store.get(db, "artifact", identifier, Artifact)
            if (artifact is None or _artifact_hash(artifact.model_dump()) != expected
                    or self.store.lookup(db, "superseded", identifier)):
                raise MentorError("handoff_source_unavailable")
            for dependency in artifact.dependencies:
                source = self.store.get(db, "source", dependency, Source)
                valid = source is not None and (transactional(db, principal, source) if callable(transactional)
                                                else policy.validate(principal, source))
                if not valid:
                    raise MentorError("handoff_source_unavailable")

    def _scope(self, handoff: Handoff, binding: ChatBinding, principal: Principal) -> PrivatePreviewScope:
        app = self.store.read("application", binding.application_id, Application)
        return PrivatePreviewScope(principal.legacy_owner_key, handoff.id, app.platform,
                                   binding.application_id, binding.external_chat_id)


def _artifact_hash(item: dict) -> str:
    fields = ("id", "conversation_id", "run_id", "actor", "kind", "origin_kind", "input_revision", "text", "source_refs",
              "dependencies", "created_at", "claims", "uncertainties", "next_steps", "stance_change", "responds_to")
    payload = {name: item.get(name) for name in fields}
    # Empty defaults preserve previews made before structured comparison existed.
    if item.get("comparison_findings"):
        payload["comparison_findings"] = item["comparison_findings"]
        payload["debate_needed"] = item.get("debate_needed")
    return digest(payload)


def _selected_handoff(view: dict, chosen: list[dict], principal_id: str) -> Handoff:
    conversation = view["conversation"]
    runs = {item.get("run_id") or conversation["run_id"] for item in chosen if item["kind"] != "user"}
    runs = runs or {conversation["run_id"]}
    if len(runs) != 1:
        raise MentorError("handoff_single_discussion_required")
    text = _result_text(chosen)
    if len(text) > 6000:
        raise MentorError("handoff_selection_too_large")
    identifier = new_id()
    run_id = next(iter(runs))
    run = next((item for item in view.get("runs", ()) if item["id"] == run_id), {})
    provenance = DiscussionProvenance(
        result_id=identifier, problem_id=conversation["id"], discussion_id=run_id,
        artifact_ids=tuple(item["id"] for item in chosen), input_revision=max(item["input_revision"] for item in chosen),
        summary_version=run.get("summary_version", conversation.get("summary_version", 0)), correction_version=conversation.get("correction_version", 0),
        recorded_at=datetime.fromtimestamp(max(item["created_at"] for item in chosen), timezone.utc).isoformat(), mentors=tuple(run.get("personas", conversation["personas"])),
        topic=conversation["question"][:200],
    )
    return Handoff(id=identifier, conversation_id=conversation["id"], principal_id=principal_id,
                   artifact_ids=provenance.artifact_ids, text=text, provenance=provenance,
                   artifact_hashes={item["id"]: _artifact_hash(item) for item in chosen})


def _revision(old: Handoff, text: str | None, target_date: date | None, draft) -> Handoff:
    identifier = new_id()
    target = target_date or (draft.target_date if draft else old.target_date)
    updated_text = old.text if text is None else text.strip()
    change = "".join(unified_diff(old.text.splitlines(keepends=True), updated_text.splitlines(keepends=True),
                                  fromfile="previous", tofile="revised"))
    previous = "已保存版本仍保留：" + draft.source_id + "。此次另存新版本。\n" if draft and draft.status == DraftStatus.COMMITTED else "旧预览已失效。\n"
    dates = f"保存日期：{draft.target_date if draft else '默认日期'} → {target or '默认日期'}\n"
    provenance = replace(old.provenance, result_id=identifier,
                         edited_by_user=old.provenance.edited_by_user or text is not None)
    return old.model_copy(update={"id": identifier, "text": updated_text, "provenance": provenance,
        "draft_id": "", "target_date": target, "superseded_by": "", "previous_handoff_id": old.id,
        "revision_note": previous + dates + ("内容修改：\n" + change + "\n" if change else "内容未变。\n") + "\n"})


def _result_text(chosen: list[dict]) -> str:
    labels = {"user_statement": "用户原话（讨论引用）", "user_plan": "用户陈述的计划（是否采纳建议以原话为准）",
              "user_feedback": "用户报告的实际反馈"}
    blocks = [labels.get(item.get("origin_kind"), "用户原话（讨论引用）") + "：" + item["text"]
              if item["kind"] == "user" else "AI 观点 · " + item["actor"] + "：" + item["text"] for item in chosen]
    uncertainty = list(dict.fromkeys(value for item in chosen for value in item.get("uncertainties", ())))
    blocks.append("保留分歧/不确定性：" + ("；".join(uncertainty) if uncertainty else "所选内容未单独列出；不代表已经验证一致。"))
    if not any(item["kind"] == "user" and item.get("origin_kind") == "user_plan" for item in chosen):
        blocks.append("我明确采纳的计划：所选内容未包含单独确认的计划。")
    if not any(item["kind"] == "user" and item.get("origin_kind") == "user_feedback" for item in chosen):
        blocks.append("实际反馈：所选内容未包含用户报告的执行反馈。")
    return "\n\n".join(blocks)
