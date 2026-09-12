"""Synthetic AI journal contracts: provenance, extraction exclusion, and confirmation."""

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.confirmation import ConfirmationContext, PrivatePreviewScope, preview_hash
from riji_agent.drafts.models import DraftOperation, DraftStatus
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.journal.content import (
    AI_RESULT, ContentSpan, DiscussionProvenance, content_spans, personal_body, render_ai_result, slice_spans,
)
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.parser import parse_note
from riji_agent.memory.journal_extract import JournalMemoryExtractor, parse_candidate
from riji_agent.memory.journal_sources import read_source
from riji_agent.memory.journal_types import JournalEvidence, JournalMemoryError, JournalMemoryPolicy
from riji_agent.mentors.handoff import Handoff, JournalHandoff
from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.models import Artifact, Conversation, MentorError
from riji_agent.retrieval.models import RetrievalLimits, ToolContext
from riji_agent.retrieval.service import RetrievalService
from test_mentor_discussions import drain, prepare, system  # noqa: F401
from test_hermes_draft_confirm import setup as gateway_setup, _msg, SECRET  # noqa: F401


@pytest.fixture
def provenance():
    return DiscussionProvenance("saved-result", "topic", "run", ("artifact",), 1, 2, 0,
                                "2026-09-11T00:00:00+00:00", ("gentle_reviewer",), "Synthetic topic")


@pytest.fixture
def journal(tmp_path):
    root = tmp_path / "synthetic-vault"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text("# {{date}}\n\n## 🧠 Notes\n")
    index = JournalIndex(tmp_path / "index.sqlite3", root)
    store = DraftStore(tmp_path / "drafts.sqlite3")
    drafts = DraftService(store, root, index)
    yield root, index, store, drafts
    store.close()
    index.close()


def _confirmed_commit(drafts, preview):
    scope = PrivatePreviewScope("owner", "session", "synthetic", "host", "private-chat")
    drafts.bind_preview(preview.draft_id, scope, "private-preview")
    draft = drafts.get_draft(preview.draft_id)
    context = ConfirmationContext(scope, draft.draft_id, preview_hash(draft), "explicit-confirm", draft.token)
    return drafts.commit_draft(draft.draft_id, user_id="owner", token=draft.token, confirmation=context)


def _commit(journal, provenance, text="SyntheticAdvice is a suggestion, not an experience."):
    root, index, store, drafts = journal
    preview = drafts.create_draft(user_id="owner", session_id="session", persona_id="gentle_reviewer",
        operations=(DraftOperation("🧠 Notes", text, AI_RESULT, provenance),))
    result = _confirmed_commit(drafts, preview)
    return root / "daily" / f"{result.target_date}.md", result


def test_ai_type_survives_draft_persistence_preview_write_and_index(journal, provenance):
    root, index, store, drafts = journal
    preview = drafts.create_draft(user_id="owner", session_id="session", persona_id="gentle_reviewer",
        operations=(DraftOperation("🧠 Notes", "AI advice: try once.", AI_RESULT, provenance),))
    assert "AI 导师讨论结果" in preview.preview_text
    operation = drafts.get_draft(preview.draft_id).operations[0]
    assert replace(operation.provenance, saved_date="") == provenance
    assert operation.provenance.saved_date == preview.target_date.isoformat()
    assert operation.content == "AI advice: try once."  # User-text polishing must not recast AI prose.
    _confirmed_commit(drafts, preview)
    path = next(root.glob("daily/*.md"))
    note = parse_note(path, root)
    ai = [span for span in note.content_spans if span.content_type == AI_RESULT]
    assert len(ai) == 1 and replace(ai[0].provenance, saved_date="") == provenance
    assert index.get(note.source_id).content_spans == note.content_spans
    assert "AI advice" not in personal_body(note.body, note.content_spans)


def test_legacy_draft_operations_remain_compatible(journal):
    _, _, store, drafts = journal
    preview = drafts.create_draft(user_id="owner", session_id="old", persona_id="gentle_reviewer",
        operations=(DraftOperation("🧠 Notes", "An ordinary personal note."),))
    store._conn.execute("UPDATE drafts SET operations=? WHERE draft_id=?",
                        (json.dumps([["🧠 Notes", "An ordinary personal note."]]), preview.draft_id))
    store._conn.commit()
    assert drafts.get_draft(preview.draft_id).operations[0].content_type == "personal_journal"


def test_notes_extraction_cannot_slice_ai_into_user_facts(journal, provenance):
    path, _ = _commit(journal, provenance, "SyntheticAdvice: I completed an imagined marathon. " * 100)
    path.write_text(path.read_text() + "\n- I actually practiced piano for twenty minutes.\n")
    root = journal[0]
    policy = JournalMemoryPolicy(root, "owner", ("🧠 Notes",), segment_chars=100, settle_seconds=0)
    source = read_source(path, policy)
    assert source.evidence
    assert all(item.content_type == "personal_journal" for item in source.evidence)
    assert "actually practiced" in " ".join(item.text for item in source.evidence)
    assert "SyntheticAdvice" not in " ".join(item.text for item in source.evidence)
    assert "imagined marathon" not in " ".join(item.text for item in source.evidence)


def test_extractor_refuses_ai_even_if_a_caller_strips_all_headings(provenance):
    class NeverModel:
        def complete(self, *args, **kwargs):
            raise AssertionError("AI material must be rejected before model use")
    evidence = JournalEvidence("e", "s", "daily/s.md", "v", "daily", "🧠 Notes", 1,
                               "2026-09-11", "I completed an imagined marathon.", AI_RESULT, provenance)
    extractor = JournalMemoryExtractor(NeverModel(), charge=lambda _: pytest.fail("must not charge"))
    assert extractor.extract(evidence) == ()
    with pytest.raises(JournalMemoryError, match="not_personal_evidence"):
        parse_candidate({}, evidence)
    # A non-empty AI provenance cannot be relabeled personal by another pipeline stage.
    assert extractor.extract(replace(evidence, content_type="personal_journal")) == ()


@pytest.mark.parametrize("text", [
    "<!-- riji:ai-discussion-result invalid -->\nI completed an imaginary project.",
    "I completed an imaginary project.\n<!-- /riji:ai-discussion-result -->",
    "### AI 导师讨论结果\nI completed an imaginary project.",
    "gentle_reviewer：I completed an imaginary project.",
])
def test_malformed_and_legacy_ai_content_stays_out_of_fact_extraction(text):
    body = "## 🧠 Notes\n" + text
    assert "imaginary project" not in personal_body(body)
    assert any(span.content_type == "unknown_ai" for span in content_spans(body))


def test_broken_ai_metadata_cannot_become_personal_after_export(provenance):
    body = render_ai_result("Imaginary achievement.", provenance).replace('"artifact_ids":["artifact"]', '"artifact_ids":[]')
    assert "Imaginary achievement" not in personal_body(body)
    assert "unknown_ai" in {span.content_type for span in content_spans(body)}


def test_exported_body_and_spans_preserve_origin_without_a_visible_title(provenance):
    body = render_ai_result("SyntheticAdvice.", provenance).replace("> [!note] AI 导师讨论结果\n", "")
    package = json.loads(json.dumps({"body": body, "content_spans": [asdict(span) for span in content_spans(body)]}))
    assert package["content_spans"][0]["content_type"] == AI_RESULT
    assert content_spans(package["body"])[0].provenance == provenance
    assert "SyntheticAdvice" not in personal_body(package["body"])


def test_retrieval_defaults_to_personal_and_explicit_ai_keeps_sliced_metadata(journal, provenance):
    path, result = _commit(journal, provenance, "SyntheticAdvice: " + "imagine a possible plan. " * 20)
    root, index, _, _ = journal
    path.write_text(path.read_text() + "\n- PersonalFact: practiced piano today.\n")
    index.update_note(path)
    service = RetrievalService(index, limits=RetrievalLimits(snippet_max_chars=90, read_note_max_chars=900))
    context = ToolContext("request", "session", "owner", "gentle_reviewer")
    assert service.search_journal(context, "SyntheticAdvice").items == ()
    assert service.search_journal(context, "PersonalFact").items
    assert "SyntheticAdvice" not in service.read_note(context, result.source_id).body
    assert not service.has_ai_discussion_evidence(context.request_id)
    ai_context = replace(context, include_ai_discussions=True)
    item = service.search_journal(ai_context, "SyntheticAdvice").items[0]
    assert item.content_type == AI_RESULT
    assert service.has_ai_discussion_evidence(context.request_id)
    assert replace(item.content_spans[0].provenance, saved_date="") == provenance
    assert all(0 <= span.start < span.end <= len(item.snippet) for span in item.content_spans)
    note = service.read_note(ai_context, result.source_id)
    assert AI_RESULT in {span.content_type for span in note.content_spans}
    timeline = service.timeline(ai_context, "SyntheticAdvice", result.target_date, result.target_date)
    assert timeline.buckets[0].entries[0].content_type == AI_RESULT


def test_frontmatter_origin_survives_index_and_extraction_without_markers(journal):
    root, index, _, _ = journal
    (root / "daily").mkdir()
    path = root / "daily/2026-09-11.md"
    path.write_text("---\ncontent_type: ai_discussion_result\n---\n## 🧠 Notes\n- ImaginedFact: completed a marathon.\n")
    note = index.update_note(path)
    assert all(span.content_type == AI_RESULT for span in index.get(note.source_id).content_spans)
    policy = JournalMemoryPolicy(root, "owner", ("🧠 Notes",), settle_seconds=0)
    assert not read_source(path, policy).evidence


def test_missing_template_anchor_never_falls_back_to_untyped_text(journal, provenance):
    root, _, _, drafts = journal
    (root / "templates/daily.md").write_text("# {{date}}\n## Something else\n")
    preview = drafts.create_draft(user_id="owner", session_id="session", persona_id="gentle_reviewer",
        operations=(DraftOperation("🧠 Notes", "An AI suggestion.", AI_RESULT, provenance),))
    with pytest.raises(DraftError) as error:
        _confirmed_commit(drafts, preview)
    assert error.value.code == DraftErrorCode.SECTION_NOT_FOUND
    assert drafts.get_draft(preview.draft_id).status == DraftStatus.AWAITING
    assert not list(root.glob("daily/*.md"))


def _handoff(system, journal):
    service, _, _, _, _, principal, _, binding = system
    discussion = prepare(system)
    drain(system, discussion.id)
    artifact = next(item for item in service.store.list("artifact", discussion.id, Artifact) if item.kind == "synthesis")
    handoffs = JournalHandoff(DiscussionHistory(service), journal[3])
    selected = handoffs.create(discussion.id, principal.id, (artifact.id,), operation_id="save-event")
    return handoffs, selected, artifact, binding


def test_handoff_event_and_confirmation_are_idempotent(system, journal):
    handoffs, selected, artifact, binding = _handoff(system, journal)
    again = handoffs.create(selected.conversation_id, selected.principal_id, (artifact.id,), operation_id="save-event")
    assert again.id == selected.id
    handoffs.preview(selected.id, binding, "preview")
    handoffs.preview(selected.id, binding, "same-preview-retry")
    handoffs.confirm(selected.id, binding, "confirm")
    assert "未重复追加" in handoffs.confirm(selected.id, binding, "repeat-confirm")
    text = next(journal[0].glob("daily/*.md")).read_text()
    assert text.count("<!-- riji:ai-discussion-result ") == 1


def test_new_run_does_not_change_an_already_displayed_save(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    handoffs.preview(selected.id, binding, "preview")
    with handoffs.store.transaction() as db:
        current = handoffs.store.get(db, "conversation", selected.conversation_id, Conversation)
        changed = current.model_copy(update={"run_id": "independent-next-run", "input_revision": current.input_revision + 1})
        handoffs.store.put(db, "conversation", changed, selected.principal_id)
    assert "保存日记" in handoffs.confirm(selected.id, binding, "confirm-old-selected-version")
    note = next(journal[0].glob("daily/*.md")).read_text()
    assert "independent-next-run" not in note


def test_correction_invalidates_preview_and_purges_unsaved_copy(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    handoffs.preview(selected.id, binding, "preview")
    with handoffs.store.transaction() as db:
        current = handoffs.store.get(db, "conversation", selected.conversation_id, Conversation)
        changed = current.model_copy(update={"correction_version": current.correction_version + 1})
        handoffs.store.put(db, "conversation", changed, selected.principal_id)
    with pytest.raises(MentorError, match="handoff_source_unavailable"):
        handoffs.confirm(selected.id, binding, "late-confirm")
    saved = handoffs.store.read("handoff", selected.id, Handoff)
    draft = journal[3].get_draft(saved.draft_id)
    assert draft.status == DraftStatus.CANCELLED and not draft.operations
    assert not list(journal[0].glob("daily/*.md"))


def test_changed_source_and_unregistered_private_identity_cannot_save(system, journal):
    handoffs, selected, artifact, binding = _handoff(system, journal)
    with pytest.raises(MentorError, match="verified_riji_private_chat_required"):
        handoffs.preview(selected.id, binding.model_copy(update={"id": "unregistered"}), "preview")
    handoffs.preview(selected.id, binding, "preview")
    with handoffs.store.transaction() as db:
        handoffs.store.put(db, "artifact", artifact.model_copy(update={"text": "Changed after preview."}), selected.conversation_id)
    with pytest.raises(MentorError, match="handoff_source_unavailable"):
        handoffs.confirm(selected.id, binding, "confirm")
    assert not list(journal[0].glob("daily/*.md"))


def test_handoff_expiry_preserves_original_result_and_rejects_write(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    handoffs.preview(selected.id, binding, "preview")
    journal[3]._now = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(DraftError) as error:
        handoffs.confirm(selected.id, binding, "expired")
    assert error.value.code == DraftErrorCode.TOKEN_EXPIRED
    assert not list(journal[0].glob("daily/*.md"))


def test_ai_result_cannot_use_the_plain_local_token_bypass(journal, provenance):
    drafts = journal[3]
    preview = drafts.create_draft(user_id="owner", session_id="session", persona_id="gentle_reviewer",
        operations=(DraftOperation("🧠 Notes", "A synthetic AI result.", AI_RESULT, provenance),))
    with pytest.raises(DraftError) as error:
        drafts.commit_draft(preview.draft_id, user_id="owner", token=preview.token)
    assert error.value.code == DraftErrorCode.PREVIEW_REQUIRED
    assert not list(journal[0].glob("daily/*.md"))


def test_callout_with_removed_machine_markers_is_still_not_a_personal_fact(provenance):
    rendered = render_ai_result("Imaginary achievement.", provenance)
    body = "\n".join(line for line in rendered.splitlines() if not line.startswith("<!--"))
    assert "Imaginary achievement" not in personal_body(body)
    assert content_spans(body)[0].content_type == "unknown_ai"


def test_source_revocation_after_preview_stops_confirmation(system, journal):
    from riji_agent.mentors.models import Source
    handoffs, selected, artifact, binding = _handoff(system, journal)
    source = Source(id="synthetic-source", owner_id=selected.principal_id, version="original", text="Synthetic evidence.",
                    kind="journal", allowed_personas=("gentle_reviewer", "blunt_coach"))
    class Sources:
        allowed = True
        def validate(self, principal, current):
            return self.allowed and current.version == "original"
    port = Sources()
    handoffs.history.service.policy.sources = port
    with handoffs.store.transaction() as db:
        handoffs.store.put(db, "source", source)
        changed = artifact.model_copy(update={"dependencies": (source.id,)})
        handoffs.store.put(db, "artifact", changed, selected.conversation_id)
    current = handoffs.create(selected.conversation_id, selected.principal_id, (artifact.id,))
    handoffs.preview(current.id, binding, "private-preview")
    port.allowed = False
    with pytest.raises(MentorError, match="handoff_source_unavailable"):
        handoffs.confirm(current.id, binding, "confirm-after-revocation")
    assert not list(journal[0].glob("daily/*.md"))


def test_local_index_snippet_carries_ai_origin_without_the_heading(journal, provenance):
    path, _ = _commit(journal, provenance, "Repeated context. " * 40 + "UniqueAdvice appears here. " * 10)
    index = journal[1]
    hit = index.search("UniqueAdvice")[0]
    assert hit.content_type == AI_RESULT
    assert any(span.provenance and replace(span.provenance, saved_date="") == provenance for span in hit.content_spans)
    assert "AI 导师讨论结果" not in hit.snippet


def test_private_revision_cancels_old_preview_and_preserves_ai_type(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    handoffs.preview(selected.id, binding, "preview")
    updated = handoffs.revise(selected.id, binding, "edit-event", text="User edited an AI suggestion.", target_date=date(2026, 8, 1))
    revised_id = updated.split("/确认转交 ")[-1]
    assert revised_id != selected.id
    assert "内容修改" in updated and "2026-08-01" in updated
    assert handoffs.revise(selected.id, binding, "edit-event", text="User edited an AI suggestion.", target_date=date(2026, 8, 1)) == updated
    with pytest.raises(MentorError, match="handoff_preview_superseded"):
        handoffs.confirm(selected.id, binding, "old-confirm")
    handoffs.confirm(revised_id, binding, "new-confirm")
    path = journal[0] / "daily/2026-08-01.md"
    assert path.exists()
    saved = next(span.provenance for span in parse_note(path, journal[0]).content_spans if span.provenance)
    assert saved.edited_by_user and saved.saved_date == "2026-08-01"
    assert "User edited" not in personal_body(path.read_text())


def test_revising_committed_result_creates_a_reviewable_independent_copy(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    handoffs.preview(selected.id, binding, "preview")
    handoffs.confirm(selected.id, binding, "confirmed-original")
    before = next(journal[0].glob("daily/*.md")).read_text()
    preview = handoffs.revise(selected.id, binding, "new-version", text="A different conditional recommendation.")
    assert "已保存版本仍保留" in preview and "内容修改" in preview
    revised_id = preview.split("/确认转交 ")[-1]
    assert next(journal[0].glob("daily/*.md")).read_text() == before
    handoffs.confirm(revised_id, binding, "confirmed-new-version")
    after = next(journal[0].glob("daily/*.md")).read_text()
    assert after.count("<!-- riji:ai-discussion-result ") == 2
    assert "A different conditional recommendation" in after


def test_group_cannot_revise_or_choose_a_diary_date(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    with pytest.raises(MentorError, match="verified_riji_private_chat_required"):
        handoffs.revise(selected.id, binding.model_copy(update={"chat_type": "group"}), "group-edit", target_date=date(2026, 8, 1))
    assert not list(journal[0].glob("daily/*.md"))


def test_plain_draft_tool_cannot_polish_away_ai_markers(journal, provenance):
    drafts = journal[3]
    with pytest.raises(DraftError) as error:
        drafts.create_draft(user_id="owner", session_id="session", persona_id="gentle_reviewer",
            operations=(DraftOperation("🧠 Notes", render_ai_result("A synthetic AI result.", provenance)),))
    assert error.value.code == DraftErrorCode.PREVIEW_REQUIRED


def test_selected_user_plan_and_feedback_are_distinguished(system, journal):
    handoffs, selected, artifact, binding = _handoff(system, journal)
    plan = artifact.model_copy(update={"id": "user-plan", "kind": "user", "origin_kind": "user_plan", "text": "I plan to practice.", "run_id": "earlier-run"})
    feedback = artifact.model_copy(update={"id": "user-feedback", "kind": "user", "origin_kind": "user_feedback", "text": "I practiced yesterday."})
    with handoffs.store.transaction() as db:
        handoffs.store.put(db, "artifact", plan, selected.conversation_id)
        handoffs.store.put(db, "artifact", feedback, selected.conversation_id)
    chosen = handoffs.create(selected.conversation_id, selected.principal_id, (artifact.id, plan.id, feedback.id))
    preview = handoffs.preview(chosen.id, binding, "show-mixed")
    assert "用户陈述的计划（是否采纳建议以原话为准）：I plan" in preview
    assert "用户报告的实际反馈：I practiced" in preview
    assert "所选内容未包含用户报告" not in preview


def test_gateway_fallback_cannot_turn_ai_summary_into_a_personal_draft(gateway_setup):
    gateway, drafts, root = gateway_setup
    class DiscussionSummary:
        request_ids = []
        def respond(self, context, *args, **kwargs):
            self.request_ids.append(context.request_id)
            return "草稿（2026-09-11）将追加：\n[🧠 Notes]\n- I completed an imaginary project.\n回复「确认保存」写入。"
        def has_ai_discussion_evidence(self, request_id):
            return request_id in self.request_ids
    gateway._responder = DiscussionSummary()
    reply = gateway.handle(SECRET, _msg("请整理成一份草稿"))
    assert "AI 讨论资料" in reply.text
    assert "imaginary project" not in reply.text
    assert not drafts.get_latest_awaiting_for_session("ou_1:gentle_reviewer:c1")
    assert not list(root.glob("daily/*.md"))


def test_gateway_correction_does_not_drop_an_ai_draft_provenance(gateway_setup, provenance):
    from riji_agent.memory.models import session_key
    gateway, drafts, root = gateway_setup
    preview = drafts.create_draft(user_id="ou_1", session_id=session_key("ou_1", "gentle_reviewer", "c1"),
        persona_id="gentle_reviewer", operations=(DraftOperation("🧠 Notes", "A synthetic AI result.", AI_RESULT, provenance),))
    reply = gateway.handle(SECRET, _msg("刚才日期错了，改成今天"))
    assert "专用转交预览" in reply.text
    latest = drafts.get_latest_awaiting_for_session(session_key("ou_1", "gentle_reviewer", "c1"))
    assert latest.draft_id == preview.draft_id and latest.operations[0].content_type == AI_RESULT
    assert not list(root.glob("daily/*.md"))


def test_ai_taint_survives_restart_but_explicit_personal_input_remains_writable(gateway_setup):
    from riji_agent.memory.models import session_key
    from riji_agent.memory.store import MemoryStore
    gateway, drafts, root = gateway_setup
    class AIAnswer:
        def respond(self, context, *args, **kwargs):
            return "Synthetic discussion advice about an imaginary accomplishment."
        def has_ai_discussion_evidence(self, request_id):
            return True
    gateway._responder = AIAnswer()
    gateway.handle(SECRET, _msg("参考历史导师讨论", event_id="ai-reference"))
    database = gateway._store._database_path
    gateway._store.close()
    gateway._store = MemoryStore(database)
    history = gateway._store.get_session_history("ou_1", "gentle_reviewer", "c1")
    assert history[-1].content_type == AI_RESULT
    class RephrasingAnswer:
        def respond(self, context, *args, **kwargs):
            assert context.ai_discussion_history
            return "草稿（2026-09-11）将追加：\n[🧠 Notes]\n- I completed an imaginary accomplishment.\n回复「确认保存」写入。"
    gateway._responder = RephrasingAnswer()
    reply = gateway.handle(SECRET, _msg("把前面的结论整理为草稿", event_id="next-request"))
    assert "不能把整理结果保存为本人经历" in reply.text
    assert not drafts.get_latest_awaiting_for_session(session_key("ou_1", "gentle_reviewer", "c1"))
    personal = gateway.handle(SECRET, _msg("帮我记录：今天练习了十分钟钢琴。", event_id="personal-original"))
    assert "草稿" in personal.text
    saved = gateway.handle(SECRET, _msg("确认保存", event_id="confirm-personal"))
    assert "已写入" in saved.text
    text = next(root.glob("daily/*.md")).read_text()
    assert "练习了十分钟钢琴" in text and "imaginary accomplishment" not in text


def test_responder_retains_ai_taint_but_does_not_replay_unchecked_ai_history(monkeypatch):
    from types import SimpleNamespace
    from riji_agent.hermes.responder import AgentResponder
    from riji_agent.memory.models import SessionMessage
    observed = {}
    class Runner:
        def __init__(self, *args, **kwargs):
            pass
        def run(self, context, question, *, history):
            observed.update(context=context, history=history)
            return SimpleNamespace(answer="Synthetic response", audit=())
    monkeypatch.setattr("riji_agent.hermes.responder.AgentRunner", Runner)
    tools = SimpleNamespace(tool_specs=lambda allowed: [])
    responder = AgentResponder(object(), tools)
    context = ToolContext("request", "session", "owner", "gentle_reviewer")
    responder.respond(context, "System", [SessionMessage("assistant", "Revoked synthetic source text", "now", AI_RESULT)], "Continue")
    assert observed["context"].ai_discussion_history
    assert not observed["context"].include_ai_discussions
    assert "Revoked synthetic source text" not in str(observed["history"])
    assert "核验当前来源" in str(observed["history"])


def test_handoff_rechecks_external_source_at_atomic_replace(system, journal, monkeypatch):
    import riji_agent.drafts.writer as writer
    from riji_agent.mentors.models import Source
    handoffs, selected, artifact, binding = _handoff(system, journal)
    source = Source(id="synthetic-source", owner_id=selected.principal_id, version="version", text="Synthetic evidence.",
                    kind="journal", allowed_personas=("gentle_reviewer", "blunt_coach"))
    class Sources:
        allowed = True
        def validate(self, principal, current):
            return self.allowed
    port = Sources()
    handoffs.history.service.policy.sources = port
    with handoffs.store.transaction() as db:
        handoffs.store.put(db, "source", source)
        handoffs.store.put(db, "artifact", artifact.model_copy(update={"dependencies": (source.id,)}), selected.conversation_id)
    current = handoffs.create(selected.conversation_id, selected.principal_id, (artifact.id,))
    handoffs.preview(current.id, binding, "preview")
    original = writer._sync_file
    def revoke_after_temporary_write(path):
        original(path)
        port.allowed = False
    monkeypatch.setattr(writer, "_sync_file", revoke_after_temporary_write)
    with pytest.raises(MentorError, match="handoff_source_unavailable"):
        handoffs.confirm(current.id, binding, "confirm-before-revocation")
    assert not list(journal[0].glob("daily/*.md"))
    assert not list(journal[0].glob("daily/*.tmp-*"))


def test_transferred_origin_correction_between_check_and_commit_is_rejected(system, journal, monkeypatch):
    from riji_agent.mentors.transfer import DiscussionTransfer, TransferSources
    from test_persistent_roundtable import apply
    service, _, _, _, _, principal, _, binding = system
    service.policy.sources = TransferSources(service.store, service.policy.sources)
    origin = prepare(system, "reference")
    drain(system, origin.id)
    conclusion = next(item for item in service.store.list("artifact", origin.id, Artifact) if item.kind == "comparison")
    statement = next(item for item in service.store.list("artifact", origin.id, Artifact) if item.kind == "user")
    history = DiscussionHistory(service)
    transfer = DiscussionTransfer(history)
    approved = transfer.preview(origin.id, principal.id, (conclusion.id,), origin.personas)
    target = service.create(binding, "A related synthetic question", personas=origin.personas, mode="reference")
    transfer.accept(approved["id"], principal.id, approved["fingerprint"], target.id)
    target = service.get(target.id, principal.id)
    result = conclusion.model_copy(update={"id": "target-result", "conversation_id": target.id,
        "run_id": target.run_id, "input_revision": target.input_revision, "dependencies": target.source_ids})
    with service.store.transaction() as db:
        service.store.put(db, "artifact", result, target.id)
    handoffs = JournalHandoff(history, journal[3])
    selected = handoffs.create(target.id, principal.id, (result.id,))
    handoffs.preview(selected.id, binding, "preview")
    original = journal[3].get_draft
    changed = []
    def correct_origin_after_initial_check(identifier):
        value = original(identifier)
        if not changed:
            changed.append(True)
            apply(system, origin, "correct", text="The original synthetic condition was wrong.", supersedes=(statement.id,))
        return value
    monkeypatch.setattr(journal[3], "get_draft", correct_origin_after_initial_check)
    with pytest.raises(MentorError, match="handoff_source_unavailable"):
        handoffs.confirm(selected.id, binding, "confirm")
    assert service.get(target.id, principal.id).correction_version == target.correction_version
    assert not list(journal[0].glob("daily/*.md"))
