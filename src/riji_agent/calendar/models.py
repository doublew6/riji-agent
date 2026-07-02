"""Calendar domain models.

Calendar writes follow the same shape as journal writes: parse into a local
draft, show a preview, and only create the external event after user
confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class CalendarDraftStatus(str, Enum):
    AWAITING = "awaiting"
    CREATING = "creating"
    CREATED = "created"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class CalendarEventDraft:
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    reminder_minutes: Optional[int] = None
    description: str = ""


@dataclass(frozen=True)
class CalendarEventResult:
    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    calendar_url: Optional[str] = None
    journal_source_id: Optional[str] = None


@dataclass(frozen=True)
class CalendarDraft:
    draft_id: str
    user_id: str
    session_id: str
    persona_id: str
    event: CalendarEventDraft
    token: str
    status: CalendarDraftStatus
    created_at: str
    expires_at: str
    provider_event_id: Optional[str] = None
    journal_source_id: Optional[str] = None
