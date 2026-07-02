"""Small deterministic parser for common Chinese calendar requests."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import replace
from datetime import date as Date
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from riji_agent.calendar.models import CalendarEventDraft
from riji_agent.timezone import local_journal_timezone

_ISO_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)?")
_MONTH_OFFSET_RE = re.compile(r"(?P<count>\d{1,2}|[一二两三四五六七八九十]+)\s*个?月后")
_WEEK_OFFSET_RE = re.compile(r"(?P<count>\d{1,2}|[一二两三四五六七八九十]+)\s*(?:周|星期)后")
_DAY_OFFSET_RE = re.compile(r"(?P<count>\d{1,2}|[一二两三四五六七八九十]+)\s*天后")
_TIME_RE = re.compile(
    r"(?P<period>上午|中午|下午|晚上|晚间|今晚|早上|凌晨)?\s*"
    r"(?P<hour>\d{1,2})\s*(?:点|:|：)"
    r"(?:(?P<half>半)|(?P<minute>\d{1,2})\s*分?)?"
)
_REMINDER_RE = re.compile(r"提前\s*(?P<minutes>\d{1,3})\s*分钟")
_DURATION_RE = re.compile(r"(?P<hours>\d+(?:\.\d+)?)\s*(?:小时|个小时)")
_TITLE_CLEAN_RE = re.compile(
    r"(今天|明天|后天|大后天|上午|中午|下午|晚上|晚间|今晚|早上|凌晨|"
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)?|"
    r"(?:\d{1,2}|[一二两三四五六七八九十]+)\s*个?月后|"
    r"(?:\d{1,2}|[一二两三四五六七八九十]+)\s*(?:周|星期|天)后|"
    r"\d{1,2}\s*(?:点|:|：)\s*(?:半|\d{1,2}\s*分?)?|"
    r"提前\s*\d{1,3}\s*分钟|提醒我|提醒|帮我|请|"
    r"在日历里|日历里|加一个|安排一次|安排|创建|新建|日程|会议|一次)"
)
_DEFAULT_FLOATING_EVENT_TIME = time(hour=9, minute=0)


class CalendarParseError(ValueError):
    """Raised when a natural language request lacks required event fields."""


def parse_calendar_request(
    text: str,
    *,
    now: datetime | None = None,
    default_duration_minutes: int = 60,
    timezone_name: str | None = None,
) -> CalendarEventDraft:
    tz = _timezone(timezone_name)
    local_now = now.astimezone(tz) if now is not None else datetime.now(tz)
    target_date = _parse_date(text, local_now.date())
    start_time = _parse_time(text)
    if start_time is None:
        if not _has_explicit_date(text):
            raise CalendarParseError("missing_time")
        start_time = _DEFAULT_FLOATING_EVENT_TIME
    start_at = datetime.combine(target_date, start_time, tzinfo=tz)
    if start_at < local_now and not _has_explicit_date(text):
        start_at += timedelta(days=1)
    duration = _parse_duration(text) or timedelta(minutes=default_duration_minutes)
    event = CalendarEventDraft(
        title=_parse_title(text),
        start_at=start_at,
        end_at=start_at + duration,
        timezone=getattr(tz, "key", str(tz)),
        reminder_minutes=_parse_reminder(text),
        description="",
    )
    return replace(event, description=_description(event))


def looks_like_calendar_request(text: str) -> bool:
    has_when = _TIME_RE.search(text) or _has_explicit_date(text)
    if not has_when:
        return False
    return any(
        word in text
        for word in ("日历", "日程", "会议", "安排", "提醒我", "创建", "新建", "复盘", "约")
    )


def _parse_date(text: str, today: Date) -> Date:
    if match := _ISO_RE.search(text):
        return Date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    if match := _MONTH_DAY_RE.search(text):
        return Date(today.year, int(match.group("month")), int(match.group("day")))
    if match := _MONTH_OFFSET_RE.search(text):
        return _add_months(today, _number(match.group("count")))
    if match := _WEEK_OFFSET_RE.search(text):
        return today + timedelta(weeks=_number(match.group("count")))
    if match := _DAY_OFFSET_RE.search(text):
        return today + timedelta(days=_number(match.group("count")))
    if "大后天" in text:
        return today + timedelta(days=3)
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    return today


def _timezone(timezone_name: str | None):
    if not timezone_name:
        return local_journal_timezone()
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Asia/Shanghai":
            return local_journal_timezone()
        raise


def _has_explicit_date(text: str) -> bool:
    return bool(
        _ISO_RE.search(text)
        or _MONTH_DAY_RE.search(text)
        or _MONTH_OFFSET_RE.search(text)
        or _WEEK_OFFSET_RE.search(text)
        or _DAY_OFFSET_RE.search(text)
    ) or any(
        word in text for word in ("今天", "明天", "后天", "大后天")
    )


def _add_months(value: Date, months: int) -> Date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return Date(year, month, day)


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[-1], 0)
    if value.endswith("十"):
        return digits.get(value[0], 1) * 10
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits[value]


def _parse_time(text: str) -> time | None:
    match = _TIME_RE.search(text)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = 30 if match.group("half") else int(match.group("minute") or 0)
    period = match.group("period") or ""
    if period in {"下午", "晚上", "晚间", "今晚"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if hour > 23 or minute > 59:
        raise CalendarParseError("invalid_time")
    return time(hour=hour, minute=minute)


def _parse_reminder(text: str) -> int | None:
    match = _REMINDER_RE.search(text)
    if match is None:
        return None
    return int(match.group("minutes"))


def _parse_duration(text: str) -> timedelta | None:
    match = _DURATION_RE.search(text)
    if match is None:
        return None
    return timedelta(minutes=int(float(match.group("hours")) * 60))


def _parse_title(text: str) -> str:
    cleaned = _TITLE_CLEAN_RE.sub(" ", text)
    cleaned = re.sub(r"[，,。；;：:、\s]+", " ", cleaned).strip()
    return cleaned or "未命名日程"


def _description(event: CalendarEventDraft) -> str:
    reminder = (
        f"提前 {event.reminder_minutes} 分钟提醒" if event.reminder_minutes is not None else "无提醒"
    )
    return f"由 riji-agent 创建；{reminder}。"
