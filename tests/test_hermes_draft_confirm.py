from pathlib import Path

import pytest

from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.journal.index import JournalIndex
from riji_agent.memory.models import session_key
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry

SECRET = "secret"
TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"


class FakeResponder:
    def respond(self, context, system_prompt, history, question) -> str:
        return "ok"


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


def _seed_draft(draft_service) -> None:
    # Simulate the model having proposed a draft in the user's default session.
    draft_service.create_draft(
        user_id="ou_1",
        session_id=session_key("ou_1", "gentle_reviewer", "c1"),
        persona_id="gentle_reviewer",
        operations=[DraftOperation("🌆 Evening", "评审通过")],
    )


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
