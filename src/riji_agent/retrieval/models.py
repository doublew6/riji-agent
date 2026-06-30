"""Request/response models and minimisation limits for the retrieval tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from enum import Enum
from typing import List, Optional, Tuple

from riji_agent.journal.models import NoteKind


class Granularity(str, Enum):
    """Time bucket size for timeline grouping."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True)
class ToolContext:
    """Identity carried by every tool call (architecture §4).

    ``session_id`` scopes the read_note evidence gate; the remaining fields are
    carried for auditing and later capability checks.
    """

    request_id: str
    session_id: str
    feishu_user_id: str
    persona_id: str


@dataclass(frozen=True)
class RetrievalLimits:
    """Caps that keep the amount of journal text leaving the device small."""

    default_top_k: int = 5
    max_top_k: int = 10
    snippet_max_chars: int = 240
    max_total_snippet_chars: int = 1500
    read_note_max_chars: int = 4000
    max_periods: int = 50
    timeline_max_hits: int = 50
    max_timeline_span_days: int = 800


@dataclass(frozen=True)
class SearchResultItem:
    source_id: str
    title: str
    kind: NoteKind
    note_date: Optional[Date]
    snippet: str


@dataclass(frozen=True)
class SearchResponse:
    request_id: str
    items: Tuple[SearchResultItem, ...] = field(default_factory=tuple)
    truncated: bool = False


@dataclass(frozen=True)
class NoteResponse:
    request_id: str
    source_id: str
    title: str
    kind: NoteKind
    note_date: Optional[Date]
    body: str
    truncated: bool


@dataclass(frozen=True)
class PeriodItem:
    source_id: str
    kind: NoteKind
    note_date: Optional[Date]
    title: str


@dataclass(frozen=True)
class PeriodsResponse:
    request_id: str
    items: Tuple[PeriodItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TimelineEntry:
    """One dated piece of evidence on a timeline; no interpretation added."""

    source_id: str
    note_date: Optional[Date]
    title: str
    snippet: str


@dataclass(frozen=True)
class TimelineBucket:
    period: str  # e.g. "2026-06-24", "2026-W26" or "2026-06"
    entries: Tuple[TimelineEntry, ...]


@dataclass(frozen=True)
class TimelineResponse:
    request_id: str
    topic: str
    granularity: Granularity
    date_from: Date
    date_to: Date
    buckets: Tuple[TimelineBucket, ...]
    notes_found: int
    empty_periods: Tuple[str, ...]  # periods in range with no evidence
    insufficient_evidence: bool
    truncated: bool


@dataclass(frozen=True)
class BeforeAfterResponse:
    request_id: str
    pivot: Date
    days: int
    topic: Optional[str]
    before: Tuple[TimelineEntry, ...]
    on: Tuple[TimelineEntry, ...]
    after: Tuple[TimelineEntry, ...]
    notes_found: int
    insufficient_evidence: bool
    truncated: bool
