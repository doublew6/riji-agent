from datetime import datetime, timedelta, timezone
from pathlib import Path

from riji_agent.calendar.models import CalendarEventResult
from riji_agent.calendar.service import CalendarService
from riji_agent.calendar.store import CalendarDraftStore
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.journal.index import JournalIndex
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry

SECRET = "secret"
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
TEMPLATE = "# {{date}}\n\n## 🧠 Notes\n\n## 🌆 Evening\n"


class NoModelResponder:
    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        raise AssertionError("local regression flows must not depend on a model")


class FakeCalendarProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.created = []
        self.user_ids = []

    def create_event(self, event, *, user_id=None):
        self.created.append(event)
        self.user_ids.append(user_id)
        return CalendarEventResult(
            event_id="evt_regression_123456",
            title=event.title,
            start_at=event.start_at,
            end_at=event.end_at,
            calendar_url="https://example.invalid/calendar/event",
        )


def _msg(text: str, *, event_id: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        feishu_user_id="ou_test",
        chat_id="chat_test",
        chat_type="p2p",
        text=text,
    )


def _local_gateway(tmp_path: Path):
    root = tmp_path / "vault"
    data = tmp_path / "data"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=data / "index.sqlite3", journal_root=root)
    provider = FakeCalendarProvider()
    draft_service = DraftService(
        DraftStore(data / "drafts.sqlite3"),
        root,
        index,
        now=lambda: datetime(2026, 7, 3, 9, 0, tzinfo=TZ),
    )
    calendar_service = CalendarService(
        CalendarDraftStore(data / "calendar.sqlite3"),
        provider,
        journal_root=root,
        index=index,
        now=lambda: datetime(2026, 7, 3, 9, 0, tzinfo=TZ),
    )
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_test"},
        registry=PersonaRegistry(),
        store=MemoryStore(data / "memory.sqlite3"),
        events=EventLog(data / "events.sqlite3"),
        responder=NoModelResponder(),
        draft_service=draft_service,
        calendar_service=calendar_service,
    )
    return gateway, provider, root, index


def test_local_regression_can_create_calendar_event_without_real_feishu(tmp_path: Path) -> None:
    gateway, provider, root, index = _local_gateway(tmp_path)

    preview = gateway.handle(
        SECRET,
        _msg("给我日历上加一个日程，3个月之后提醒我处理示例账户余额", event_id="cal-preview"),
    )
    created = gateway.handle(SECRET, _msg("确认创建", event_id="cal-confirm"))

    assert "我理解为这条日程" in preview.text
    assert "确认创建" in preview.text
    assert "已创建日程" in created.text
    assert provider.user_ids == ["ou_test"]
    assert len(provider.created) == 1
    assert provider.created[0].title == "处理示例账户余额"
    assert provider.created[0].start_at.isoformat() == "2026-10-03T09:00:00+08:00"
    assert not (root / "daily" / "2026-10-03.md").exists()
    index.close()


def test_local_regression_corrected_draft_content_is_saved(tmp_path: Path) -> None:
    gateway, provider, root, index = _local_gateway(tmp_path)

    draft = gateway.handle(
        SECRET,
        _msg("帮忙记录，今天去了地点甲，整理了一些物品。", event_id="draft-preview"),
    )
    revised = gateway.handle(
        SECRET,
        _msg("不是地点甲，是地点乙，Place B", event_id="draft-correct"),
    )
    confirmed = gateway.handle(SECRET, _msg("确认保存", event_id="draft-confirm"))

    assert "草稿" in draft.text
    assert "已按你的纠正重新起草" in revised.text
    assert "地点乙，Place B" in revised.text
    assert "已写入" in confirmed.text
    assert provider.created == []
    text = (root / "daily" / "2026-07-03.md").read_text(encoding="utf-8")
    assert "地点乙，Place B" in text
    assert "地点甲" not in text
    index.close()


def test_local_regression_journal_record_is_not_routed_to_calendar(tmp_path: Path) -> None:
    gateway, provider, root, index = _local_gateway(tmp_path)

    preview = gateway.handle(
        SECRET,
        _msg(
            "帮忙记录，今天做了一个选择。具体安排如下：\n"
            "1. 等任务结束后去运动；\n"
            "2. 调整工作环境。",
            event_id="journal-preview",
        ),
    )
    confirmed = gateway.handle(SECRET, _msg("确认保存", event_id="journal-confirm"))

    assert "草稿" in preview.text
    assert "确认保存" in preview.text
    assert "确认创建" not in preview.text
    assert "已写入" in confirmed.text
    assert provider.created == []
    text = (root / "daily" / "2026-07-03.md").read_text(encoding="utf-8")
    assert "具体安排如下" in text
    assert "日程：" not in text
    index.close()
