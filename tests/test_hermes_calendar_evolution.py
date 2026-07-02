from datetime import datetime, timedelta, timezone
from pathlib import Path

from riji_agent.calendar.models import CalendarEventResult
from riji_agent.calendar.service import CalendarService
from riji_agent.calendar.store import CalendarDraftStore
from riji_agent.evolution.service import EvolutionService
from riji_agent.evolution.store import EvolutionProposalStore
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.journal.index import JournalIndex
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry

SECRET = "secret"
TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


class ExplodingResponder:
    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        raise AssertionError("this path should not call the model")


class FakeCalendarProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.created = []

    def create_event(self, event):
        self.created.append(event)
        return CalendarEventResult(
            event_id="evt_fake_123456",
            title=event.title,
            start_at=event.start_at,
            end_at=event.end_at,
        )


def _msg(text: str, *, event_id: str = "e1") -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        feishu_user_id="ou_1",
        chat_id="c1",
        chat_type="p2p",
        text=text,
    )


def _gateway(tmp_path: Path):
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=tmp_path / "data" / "idx.sqlite3", journal_root=root)
    provider = FakeCalendarProvider()
    calendar = CalendarService(
        CalendarDraftStore(tmp_path / "data" / "calendar.sqlite3"),
        provider,
        journal_root=root,
        index=index,
        now=lambda: datetime(2026, 7, 2, 10, 0, tzinfo=TZ),
    )
    evolution = EvolutionService(
        EvolutionProposalStore(tmp_path / "data" / "evolution.sqlite3"),
        now=lambda: datetime(2026, 7, 2, 10, 0, tzinfo=TZ),
    )
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=MemoryStore(tmp_path / "data" / "mem.sqlite3"),
        events=EventLog(tmp_path / "data" / "events.sqlite3"),
        responder=ExplodingResponder(),
        calendar_service=calendar,
        evolution_service=evolution,
    )
    return gateway, provider, root, index


def test_calendar_request_previews_then_confirm_creates_and_links(tmp_path: Path) -> None:
    gateway, provider, root, index = _gateway(tmp_path)

    preview = gateway.handle(
        SECRET,
        _msg("明天下午 3 点安排一次项目复盘，提前 10 分钟提醒", event_id="cal-1"),
    )

    assert "我理解为这条日程" in preview.text
    assert "确认创建" in preview.text
    assert provider.created == []

    created = gateway.handle(SECRET, _msg("确认创建", event_id="cal-2"))

    assert "已创建日程" in created.text
    assert provider.created[0].title == "项目复盘"
    assert "日程：项目复盘" in (root / "daily" / "2026-07-03.md").read_text(encoding="utf-8")
    index.close()


def test_calendar_confirm_without_pending_draft_is_safe(tmp_path: Path) -> None:
    gateway, provider, _root, index = _gateway(tmp_path)

    reply = gateway.handle(SECRET, _msg("确认创建", event_id="cal-miss"))

    assert "没有待确认的日程草稿" in reply.text
    assert provider.created == []
    index.close()


def test_hermes_evolution_creates_safe_proposal_without_model_call(tmp_path: Path) -> None:
    gateway, _provider, _root, index = _gateway(tmp_path)

    proposal = gateway.handle(SECRET, _msg("/hermes 把反复出现的问题整理成 issue 草稿"))

    assert "已生成一条安全改进提案" in proposal.text
    assert "整理改进 issue 草案" in proposal.text
    assert "聊天原文" not in proposal.text

    approved = gateway.handle(SECRET, _msg("确认改进", event_id="ev-2"))
    assert "已标记为已批准" in approved.text
    index.close()


def test_hermes_evolution_reject_without_pending_is_safe(tmp_path: Path) -> None:
    gateway, _provider, _root, index = _gateway(tmp_path)

    reply = gateway.handle(SECRET, _msg("拒绝改进"))

    assert "没有待确认的改进提案" in reply.text
    index.close()
