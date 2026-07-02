from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from riji_agent.calendar.models import CalendarEventResult
from riji_agent.calendar.parser import parse_calendar_request
from riji_agent.calendar.service import CalendarService
from riji_agent.calendar.store import CalendarDraftStore
from riji_agent.journal.index import JournalIndex

TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


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


def _parts(tmp_path: Path):
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    index = JournalIndex(database_path=tmp_path / "data" / "idx.sqlite3", journal_root=root)
    provider = FakeCalendarProvider()
    service = CalendarService(
        CalendarDraftStore(tmp_path / "data" / "calendar.sqlite3"),
        provider,
        journal_root=root,
        index=index,
        now=lambda: datetime(2026, 7, 2, 10, 0, tzinfo=TZ),
    )
    return service, provider, root, index


def test_parse_calendar_request_uses_local_relative_date_and_reminder() -> None:
    event = parse_calendar_request(
        "明天下午 3 点安排一次项目复盘，提前 10 分钟提醒",
        now=datetime(2026, 7, 2, 10, 0, tzinfo=TZ),
        timezone_name="Asia/Shanghai",
    )

    assert event.title == "项目复盘"
    assert event.start_at.isoformat() == "2026-07-03T15:00:00+08:00"
    assert event.end_at.isoformat() == "2026-07-03T16:00:00+08:00"
    assert event.reminder_minutes == 10


def test_calendar_confirm_creates_provider_event_and_links_journal(tmp_path: Path) -> None:
    service, provider, root, index = _parts(tmp_path)
    draft = service.create_draft_from_text(
        user_id="ou_1",
        session_id="s1",
        persona_id="gentle",
        text="明天下午 3 点安排一次项目复盘，提前 10 分钟提醒",
    )

    assert provider.created == []
    result = service.confirm(draft.draft_id, user_id="ou_1")

    assert provider.created[0].title == "项目复盘"
    assert result.event_id == "evt_fake_123456"
    assert result.journal_source_id == "riji/daily/2026-07-03"
    text = (root / "daily" / "2026-07-03.md").read_text(encoding="utf-8")
    assert "日程：项目复盘" in text
    assert "evt_...3456" in text
    index.close()


def test_calendar_confirm_rejects_wrong_user(tmp_path: Path) -> None:
    service, _provider, _root, index = _parts(tmp_path)
    draft = service.create_draft_from_text(
        user_id="ou_1",
        session_id="s1",
        persona_id="gentle",
        text="明天下午 3 点安排一次项目复盘",
    )

    with pytest.raises(Exception) as err:
        service.confirm(draft.draft_id, user_id="ou_other")

    assert "calendar_draft_not_found" in str(err.value)
    index.close()
