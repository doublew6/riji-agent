"""Calendar draft lifecycle and journal linking."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from riji_agent.calendar.models import (
    CalendarDraft,
    CalendarDraftStatus,
    CalendarEventDraft,
    CalendarEventResult,
)
from riji_agent.calendar.parser import parse_calendar_request
from riji_agent.calendar.providers import CalendarProvider, CalendarProviderError
from riji_agent.calendar.store import CalendarDraftStore
from riji_agent.drafts.errors import DraftError
from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.writer import commit_operations
from riji_agent.journal.index import JournalIndex
from riji_agent.timezone import local_journal_timezone


class CalendarError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _default_now() -> datetime:
    return datetime.now(local_journal_timezone())


class CalendarService:
    def __init__(
        self,
        store: CalendarDraftStore,
        provider: Optional[CalendarProvider],
        *,
        journal_root: Path,
        index: JournalIndex,
        ttl_minutes: int = 30,
        default_duration_minutes: int = 60,
        now: Callable[[], datetime] = _default_now,
    ) -> None:
        self._store = store
        self._provider = provider
        self._journal_root = Path(journal_root)
        self._index = index
        self._ttl = timedelta(minutes=ttl_minutes)
        self._default_duration_minutes = default_duration_minutes
        self._now = now

    def create_draft_from_text(
        self,
        *,
        user_id: str,
        session_id: str,
        persona_id: str,
        text: str,
    ) -> CalendarDraft:
        event = parse_calendar_request(
            text,
            now=self._now(),
            default_duration_minutes=self._default_duration_minutes,
        )
        now = self._now()
        draft = CalendarDraft(
            draft_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            persona_id=persona_id,
            event=event,
            token=uuid.uuid4().hex,
            status=CalendarDraftStatus.AWAITING,
            created_at=now.isoformat(),
            expires_at=(now + self._ttl).isoformat(),
        )
        self._store.save(draft)
        return draft

    def latest_awaiting_for_session(self, session_id: str) -> Optional[CalendarDraft]:
        return self._store.latest_awaiting_for_session(session_id)

    def confirm_latest(self, *, user_id: str, session_id: str) -> CalendarEventResult:
        draft = self._store.latest_awaiting_for_session(session_id)
        if draft is None:
            raise CalendarError("no_pending_calendar_draft")
        return self.confirm(draft.draft_id, user_id=user_id)

    def confirm(self, draft_id: str, *, user_id: str) -> CalendarEventResult:
        draft = self._store.get(draft_id)
        if draft is None or draft.user_id != user_id:
            raise CalendarError("calendar_draft_not_found")
        if draft.status is not CalendarDraftStatus.AWAITING:
            raise CalendarError("calendar_draft_not_awaiting")
        if self._now() > datetime.fromisoformat(draft.expires_at):
            self._store.save(dataclasses.replace(draft, status=CalendarDraftStatus.EXPIRED))
            raise CalendarError("calendar_draft_expired")
        if self._provider is None:
            raise CalendarError("calendar_provider_disabled")
        if not self._store.claim_for_create(draft_id):
            raise CalendarError("calendar_draft_not_awaiting")

        try:
            result = self._provider.create_event(draft.event)
            journal_source_id = self._link_to_journal(draft.event, result)
        except CalendarProviderError as exc:
            self._store.save(dataclasses.replace(draft, status=CalendarDraftStatus.AWAITING))
            raise CalendarError(exc.code) from exc
        except Exception as exc:
            self._store.save(dataclasses.replace(draft, status=CalendarDraftStatus.AWAITING))
            raise CalendarError("calendar_create_failed") from exc

        self._store.save(
            dataclasses.replace(
                draft,
                status=CalendarDraftStatus.CREATED,
                provider_event_id=result.event_id,
                journal_source_id=journal_source_id,
            )
        )
        return dataclasses.replace(result, journal_source_id=journal_source_id)

    def render_preview(self, draft: CalendarDraft) -> str:
        event = draft.event
        lines = [
            "我理解为这条日程：",
            f"标题：{event.title}",
            f"时间：{_format_dt(event.start_at)} - {_format_time(event.end_at)}",
            f"提醒：{_format_reminder(event.reminder_minutes)}",
            f"关联日记：[[riji/daily/{event.start_at.date().isoformat()}]]",
            "",
            "回复「确认创建」写入日历。",
        ]
        return "\n".join(lines)

    def _link_to_journal(
        self, event: CalendarEventDraft, result: CalendarEventResult
    ) -> Optional[str]:
        content = (
            f"日程：{event.title}（{_format_dt(event.start_at)} - {_format_time(event.end_at)}，"
            f"provider=feishu，event_id={_safe_event_id(result.event_id)}）"
        )
        try:
            outcome = commit_operations(
                self._journal_root,
                event.start_at.date(),
                [DraftOperation("Notes", content)],
            )
        except DraftError:
            return None
        try:
            self._index.update_note(outcome.path)
        except Exception:
            pass
        return outcome.source_id


def _format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _format_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _format_reminder(minutes: Optional[int]) -> str:
    if minutes is None:
        return "无"
    return f"提前 {minutes} 分钟"


def _safe_event_id(event_id: str) -> str:
    if len(event_id) <= 8:
        return event_id
    return f"{event_id[:4]}...{event_id[-4:]}"
