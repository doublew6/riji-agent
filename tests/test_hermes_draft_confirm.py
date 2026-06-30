from pathlib import Path

import pytest

from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import (
    HermesGateway,
    is_draft_correction_request,
    parse_confirm_command,
    parse_fast_draft_request,
)
from riji_agent.hermes.models import IncomingMessage
from riji_agent.journal.index import JournalIndex
from riji_agent.memory.models import session_key
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry

SECRET = "secret"
TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"


class FakeResponder:
    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        return "ok"


class ExplodingResponder:
    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        raise AssertionError("fast draft path should not call the model")


def _msg(text: str, *, event_id: str = "e1", chat_type: str = "p2p") -> IncomingMessage:
    return IncomingMessage(event_id=event_id, feishu_user_id="ou_1", chat_id="c1", chat_type=chat_type, text=text)


@pytest.fixture
def setup(tmp_path: Path):
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=tmp_path / "d" / "idx.sqlite3", journal_root=root)
    draft_service = DraftService(DraftStore(tmp_path / "d" / "drafts.sqlite3"), root, index)
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "d" / "mem.sqlite3"),
        events=EventLog(tmp_path / "d" / "events.sqlite3"),
        responder=FakeResponder(),
        draft_service=draft_service,
    )
    yield gateway, draft_service, root
    index.close()


def _seed_draft(draft_service, persona: str = "gentle_reviewer") -> str:
    # Simulate the model having proposed a draft in the given persona's session.
    preview = draft_service.create_draft(
        user_id="ou_1",
        session_id=session_key("ou_1", persona, "c1"),
        persona_id=persona,
        operations=[DraftOperation("🌆 Evening", "评审通过")],
    )
    return preview.draft_id


def test_confirm_commits_the_pending_draft(setup) -> None:
    gateway, draft_service, root = setup
    _seed_draft(draft_service)
    reply = gateway.handle(SECRET, _msg("确认保存"))
    assert "已写入" in reply.text
    assert "riji/daily/" in reply.text
    assert list((root / "daily").glob("*.md"))  # file written


def test_confirm_without_pending_draft(setup) -> None:
    gateway, _draft_service, _root = setup
    reply = gateway.handle(SECRET, _msg("确认保存"))
    assert "没有待确认的草稿" in reply.text


def test_duplicate_confirmation_does_not_write_twice(setup) -> None:
    gateway, draft_service, root = setup
    _seed_draft(draft_service)
    gateway.handle(SECRET, _msg("确认保存", event_id="e1"))
    # a second confirmation finds no awaiting draft and never re-writes
    second = gateway.handle(SECRET, _msg("确认保存", event_id="e2"))
    assert "没有待确认的草稿" in second.text
    text = next((root / "daily").glob("*.md")).read_text(encoding="utf-8")
    assert text.count("- 评审通过") == 1


def test_group_chat_cannot_confirm(setup) -> None:
    gateway, draft_service, _root = setup
    _seed_draft(draft_service)
    with pytest.raises(AuthError) as err:
        gateway.handle(SECRET, _msg("确认保存", chat_type="group"))
    assert err.value.code is AuthErrorCode.GROUP_CHAT_DENIED


def test_explicit_draft_id_confirms_across_persona_switch(setup) -> None:
    gateway, draft_service, root = setup
    # Draft was proposed under a different persona's session than the current one.
    draft_id = _seed_draft(draft_service, persona="blunt_coach")
    # Implicit confirm in the current (default) session can't locate it.
    miss = gateway.handle(SECRET, _msg("确认保存", event_id="e1"))
    assert "没有待确认的草稿" in miss.text
    # Explicit id still commits, exactly once.
    reply = gateway.handle(SECRET, _msg(f"确认保存 {draft_id}", event_id="e2"))
    assert "已写入" in reply.text
    text = next((root / "daily").glob("*.md")).read_text(encoding="utf-8")
    assert text.count("- 评审通过") == 1


def test_explicit_draft_id_of_another_user_is_not_disclosed(setup) -> None:
    gateway, draft_service, root = setup
    other = draft_service.create_draft(
        user_id="ou_other",
        session_id=session_key("ou_other", "gentle_reviewer", "c9"),
        persona_id="gentle_reviewer",
        operations=[DraftOperation("🌆 Evening", "别人的私密内容")],
    )
    reply = gateway.handle(SECRET, _msg(f"确认保存 {other.draft_id}"))
    assert "未找到该草稿" in reply.text
    assert not list((root / "daily").glob("*.md"))  # nothing written for another user


def test_fast_draft_request_creates_preview_without_model_call(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=tmp_path / "d" / "idx.sqlite3", journal_root=root)
    draft_service = DraftService(DraftStore(tmp_path / "d" / "drafts.sqlite3"), root, index)
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "d" / "mem.sqlite3"),
        events=EventLog(tmp_path / "d" / "events.sqlite3"),
        responder=ExplodingResponder(),
        draft_service=draft_service,
    )

    reply = gateway.handle(
        SECRET,
        _msg(
            "我还要你再尝试一下，在日记里记录一下。\n\n"
            "我今天把示例工具订阅从基础套餐升级为专业套餐。"
        ),
    )

    assert "草稿" in reply.text
    assert "示例工具订阅" in reply.text
    assert "确认保存" in reply.text
    assert draft_service.get_latest_awaiting_for_session(
        session_key("ou_1", "gentle_reviewer", "c1")
    )
    index.close()


def test_fast_draft_request_preview_is_lightly_polished(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=tmp_path / "d" / "idx.sqlite3", journal_root=root)
    draft_service = DraftService(DraftStore(tmp_path / "d" / "drafts.sqlite3"), root, index)
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "d" / "mem.sqlite3"),
        events=EventLog(tmp_path / "d" / "events.sqlite3"),
        responder=ExplodingResponder(),
        draft_service=draft_service,
    )

    reply = gateway.handle(
        SECRET,
        _msg("帮我记录一下，今天我去三里屯理发了，今天的费用还是120，明天之后就会涨到140"),
    )

    assert "今天我去三里屯理发了，今天的费用还是120，明天之后就会涨到140" in reply.text
    assert "一下，今天" not in reply.text
    index.close()


def test_correction_rewrites_latest_draft_to_today_notes(setup) -> None:
    gateway, draft_service, root = setup
    _seed_draft(draft_service, persona="gentle_reviewer")

    revised = gateway.handle(
        SECRET,
        _msg("这个时间是不对的。今天是29号，并且默认放在 Notes 这个框架下", event_id="fix"),
    )
    assert "已按你的纠正重新起草" in revised.text
    assert "[Notes]" in revised.text

    confirm = gateway.handle(SECRET, _msg("确认保存", event_id="confirm-fix"))
    assert "已写入" in confirm.text
    text = next((root / "daily").glob("*.md")).read_text(encoding="utf-8")
    assert "## 🧠 Notes" in text
    assert text.index("## 🧠 Notes") < text.index("- 评审通过")


def test_commit_filesystem_error_returns_safe_reply(setup, monkeypatch) -> None:
    gateway, draft_service, _root = setup
    _seed_draft(draft_service)

    def fail_commit(*args, **kwargs):
        raise OSError("resource deadlock avoided")

    monkeypatch.setattr(draft_service, "commit_draft", fail_commit)
    reply = gateway.handle(SECRET, _msg("确认保存"))

    assert "本地日记文件暂时不可读写" in reply.text


def test_parse_confirm_command_recognises_optional_id() -> None:
    assert parse_confirm_command("确认保存").draft_id is None
    assert parse_confirm_command("确认保存 abc123").draft_id == "abc123"
    assert parse_confirm_command("/确认 abc").draft_id == "abc"
    # a normal message that merely contains 确认 is not a confirmation
    assert parse_confirm_command("确认一下我昨天写了什么") is None
    assert parse_confirm_command("今天天气不错") is None


def test_parse_fast_draft_request_extracts_explicit_content() -> None:
    assert parse_fast_draft_request("记录一下今天：完成了发布") == "今天：完成了发布"
    assert parse_fast_draft_request("帮我记录\n\n今天升级了示例服务") == "今天升级了示例服务"
    assert parse_fast_draft_request("确认一下我昨天写了什么") is None


def test_is_draft_correction_request() -> None:
    assert is_draft_correction_request("这个时间是不对的。今天是29号，放 Notes")
    assert not is_draft_correction_request("今天写了什么？")
