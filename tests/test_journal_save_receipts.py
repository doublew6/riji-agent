"""Regressions for saved journals being mistaken for missing drafts."""

import pytest

from riji_agent.drafts.template import section_contains_entry
from riji_agent.hermes.gateway import (
    is_draft_verification_request, reply_requests_draft_confirmation,
)
from riji_agent.memory.models import session_key
from test_hermes_draft_confirm import (
    ExplodingResponder, SECRET, StaticResponder, _msg, _seed_draft, setup,
)


@pytest.mark.parametrize("question", [
    "以上内容记录到日记里了吗？我好像没看到",
    "这条记录成功了吗？",
    "你记到日记里了吗？",
])
def test_save_status_question_checks_file_without_model(setup, question):
    gateway, drafts, root = setup
    _seed_draft(drafts)
    gateway.handle(SECRET, _msg("确认保存", event_id="confirm"))
    gateway._responder = ExplodingResponder()
    reply = gateway.handle(SECRET, _msg(question, event_id="check"))
    assert "已从目标日记文件重新读取并校验" in reply.text
    assert next((root / "daily").glob("*.md")).read_text().count("评审通过") == 1


def test_save_receipt_is_in_model_history_once_and_persona_scoped(setup):
    gateway, drafts, _root = setup
    _seed_draft(drafts)
    message = _msg("确认保存", event_id="confirm")
    reply = gateway.handle(SECRET, message)
    gateway.handle(SECRET, message)
    history = gateway._store.get_session_history("ou_1", "gentle_reviewer", "c1")
    assert [(m.role, m.content) for m in history] == [
        ("user", message.text), ("assistant", reply.text),
    ]
    assert not gateway._store.get_session_history("ou_1", "future_self", "c1")


@pytest.mark.parametrize("entry", ["评审通过", "* 评审通过", "+ 评审通过"])
def test_format_change_does_not_restore_duplicate_entry(setup, entry):
    gateway, drafts, root = setup
    _seed_draft(drafts)
    gateway.handle(SECRET, _msg("确认保存", event_id="confirm"))
    note = next((root / "daily").glob("*.md"))
    text = note.read_text().replace("- 评审通过", entry)
    note.write_text(text)
    result = drafts.ensure_latest_commit(
        user_id="ou_1", session_id=session_key("ou_1", "gentle_reviewer", "c1"),
    )
    assert result.verified and not result.repaired
    assert note.read_text() == text


@pytest.mark.parametrize("text", [
    "## Notes\n尚未评审通过\n",
    "## Notes\n评审通过了吗？\n",
    "## Notes\n\n## Evening\n评审通过\n",
    "## Notes\n> 评审通过\n",
])
def test_verification_requires_complete_entry_in_correct_section(text):
    assert not section_contains_entry(text, "Notes", "评审通过")


def test_explanation_of_completed_confirmation_is_not_a_new_preview(setup):
    gateway, drafts, _root = setup
    _seed_draft(drafts)
    gateway.handle(SECRET, _msg("确认保存", event_id="confirm"))
    explanation = "刚才的草稿已收到你的「确认保存」，所以现在没有待确认草稿。"
    gateway._responder = StaticResponder(explanation)
    reply = gateway.handle(SECRET, _msg("解释一下刚才的流程", event_id="explain"))
    assert reply.text == explanation
    assert not reply_requests_draft_confirmation(explanation)


def test_followup_to_false_missing_draft_reply_checks_saved_file(setup):
    gateway, drafts, _root = setup
    _seed_draft(drafts)
    gateway.handle(SECRET, _msg("确认保存", event_id="confirm"))
    gateway._store.append_message(
        "ou_1", "gentle_reviewer", "c1", "assistant",
        "我没有成功创建可确认草稿。请重新发送「帮我记录：...」。",
    )
    gateway._responder = ExplodingResponder()
    reply = gateway.handle(SECRET, _msg("为什么没有创建。", event_id="why"))
    assert "已从目标日记文件重新读取并校验" in reply.text


def test_duplicate_confirmation_returns_verified_receipt(setup):
    gateway, drafts, root = setup
    _seed_draft(drafts)
    gateway.handle(SECRET, _msg("确认保存", event_id="confirm"))
    reply = gateway.handle(SECRET, _msg("确认保存", event_id="confirm-again"))
    assert "已从目标日记文件重新读取并校验" in reply.text
    assert next((root / "daily").glob("*.md")).read_text().count("评审通过") == 1


def test_new_pending_draft_is_not_reported_as_previous_saved_draft(setup):
    gateway, drafts, root = setup
    _seed_draft(drafts)
    gateway.handle(SECRET, _msg("确认保存", event_id="confirm"))
    new_id = _seed_draft(drafts)
    reply = gateway.handle(SECRET, _msg("这条记录成功了吗？", event_id="check"))
    assert "最近这份草稿尚未成功提交" in reply.text
    assert new_id in reply.text
    assert drafts.get_draft(new_id).status.value == "awaiting_confirmation"
    assert next((root / "daily").glob("*.md")).read_text().count("评审通过") == 1


@pytest.mark.parametrize("text", [
    "帮我记录：今天没有保存工作文件，我有点懊恼。",
    "帮我记录：为什么保存失败？下次要检查。",
    "这篇记录是一个成功案例。",
    "我今天记录了读书心得。",
])
def test_new_content_is_not_a_save_status_question(text):
    assert not is_draft_verification_request(text)


def test_past_confirmation_explanation_is_not_a_confirmation_request():
    assert not reply_requests_draft_confirmation(
        "你回复「确认保存」后，草稿已经提交。"
    )
