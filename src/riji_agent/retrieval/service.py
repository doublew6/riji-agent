"""Retrieval tools exposed to Hermes/DeepSeek.

This layer turns the local index into a small set of read-only tools and is the
enforcement point for minimisation and privacy (architecture §6):

- private notes are never returned to the cloud;
- results are capped in count, per-snippet length and total length;
- ``read_note`` is only allowed for a source already surfaced by a search in
  the same session (the evidence gate);
- tools accept stable ``source_id`` values only, never filesystem paths.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date as Date
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from riji_agent.journal.index import JournalIndex, SearchHit
from riji_agent.retrieval.errors import RetrievalError, RetrievalErrorCode
from riji_agent.retrieval.models import (
    BeforeAfterResponse,
    Granularity,
    NoteResponse,
    PeriodItem,
    PeriodsResponse,
    RetrievalLimits,
    SearchResponse,
    SearchResultItem,
    TimelineBucket,
    TimelineEntry,
    TimelineResponse,
    ToolContext,
)
from riji_agent.retrieval.periods import enumerate_period_labels, period_label
from riji_agent.journal.models import NoteKind


class RetrievalService:
    def __init__(self, index: JournalIndex, *, limits: Optional[RetrievalLimits] = None) -> None:
        self._index = index
        self._limits = limits or RetrievalLimits()
        self._evidence: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- search_journal

    def search_journal(
        self,
        context: ToolContext,
        query: str,
        *,
        date_from: Optional[Date] = None,
        date_to: Optional[Date] = None,
        tags: Optional[Sequence[str]] = None,
        top_k: Optional[int] = None,
    ) -> SearchResponse:
        cleaned = (query or "").strip()
        if not cleaned:
            raise RetrievalError(RetrievalErrorCode.INVALID_QUERY, "query must not be empty")

        limit = self._clamp_top_k(top_k)
        try:
            hits = self._index.search(
                cleaned,
                limit=limit,
                include_private=False,  # private content never leaves the device
                date_from=date_from,
                date_to=date_to,
                tags=tags,
            )
        except sqlite3.OperationalError as exc:
            raise RetrievalError(
                RetrievalErrorCode.INVALID_QUERY, "query could not be parsed"
            ) from exc

        items: List[SearchResultItem] = []
        total = 0
        truncated = False
        for hit in hits:
            snippet = hit.snippet[: self._limits.snippet_max_chars]
            if total + len(snippet) > self._limits.max_total_snippet_chars:
                truncated = True
                break
            total += len(snippet)
            items.append(
                SearchResultItem(
                    source_id=hit.source_id,
                    title=hit.title,
                    kind=hit.kind,
                    note_date=hit.note_date,
                    snippet=snippet,
                )
            )

        self._record_evidence(context.session_id, (item.source_id for item in items))
        return SearchResponse(
            request_id=context.request_id, items=tuple(items), truncated=truncated
        )

    # --------------------------------------------------------------- read_note

    def read_note(self, context: ToolContext, source_id: str) -> NoteResponse:
        if not self._has_evidence(context.session_id, source_id):
            raise RetrievalError(
                RetrievalErrorCode.NO_EVIDENCE,
                "read_note requires a prior search that returned this source",
            )

        note = self._index.get(source_id)
        if note is None:
            raise RetrievalError(RetrievalErrorCode.NOT_FOUND, "note not found")
        if note.private:
            raise RetrievalError(
                RetrievalErrorCode.PRIVATE_BLOCKED, "note is private and cannot be returned"
            )

        body = note.body
        truncated = len(body) > self._limits.read_note_max_chars
        return NoteResponse(
            request_id=context.request_id,
            source_id=note.source_id,
            title=note.title,
            kind=note.kind,
            note_date=note.note_date,
            body=body[: self._limits.read_note_max_chars],
            truncated=truncated,
        )

    # ------------------------------------------------------------- list_periods

    def list_periods(
        self,
        context: ToolContext,
        *,
        kind: Optional[NoteKind] = None,
        date_from: Optional[Date] = None,
        date_to: Optional[Date] = None,
    ) -> PeriodsResponse:
        summaries = self._index.list_notes(
            kind=kind,
            date_from=date_from,
            date_to=date_to,
            include_private=False,
            limit=self._limits.max_periods,
        )
        items = tuple(
            PeriodItem(
                source_id=summary.source_id,
                kind=summary.kind,
                note_date=summary.note_date,
                title=summary.title,
            )
            for summary in summaries
        )
        return PeriodsResponse(request_id=context.request_id, items=items)

    # ----------------------------------------------------------------- timeline

    def timeline(
        self,
        context: ToolContext,
        topic: str,
        date_from: Date,
        date_to: Date,
        granularity: Granularity = Granularity.DAY,
    ) -> TimelineResponse:
        """Group topic evidence into day/week/month buckets over a date range.

        Returns only dated evidence and structural coverage; it never
        synthesises conclusions about the evidence.
        """
        cleaned = (topic or "").strip()
        if not cleaned:
            raise RetrievalError(RetrievalErrorCode.INVALID_QUERY, "topic must not be empty")
        if date_from > date_to:
            raise RetrievalError(
                RetrievalErrorCode.INVALID_QUERY, "date_from must not be after date_to"
            )
        if (date_to - date_from).days > self._limits.max_timeline_span_days:
            raise RetrievalError(RetrievalErrorCode.INVALID_QUERY, "date range is too large")

        hits = self._search_or_raise(
            cleaned, date_from=date_from, date_to=date_to, limit=self._limits.timeline_max_hits
        )
        entries, capped = self._cap_entries(hits)
        truncated = capped or len(hits) >= self._limits.timeline_max_hits

        buckets_map: Dict[str, List[TimelineEntry]] = {}
        for entry in entries:
            assert entry.note_date is not None  # date filter excludes undated notes
            buckets_map.setdefault(period_label(entry.note_date, granularity), []).append(entry)

        buckets = tuple(
            TimelineBucket(label, tuple(buckets_map[label])) for label in sorted(buckets_map)
        )
        covered = set(buckets_map)
        empty_periods = tuple(
            label
            for label in enumerate_period_labels(date_from, date_to, granularity)
            if label not in covered
        )
        notes_found = len(entries)
        self._record_evidence(context.session_id, (entry.source_id for entry in entries))
        return TimelineResponse(
            request_id=context.request_id,
            topic=cleaned,
            granularity=granularity,
            date_from=date_from,
            date_to=date_to,
            buckets=buckets,
            notes_found=notes_found,
            empty_periods=empty_periods,
            insufficient_evidence=notes_found == 0,
            truncated=truncated,
        )

    def find_before_after(
        self,
        context: ToolContext,
        pivot: Date,
        days: int,
        topic: Optional[str] = None,
    ) -> BeforeAfterResponse:
        """Find evidence in a ``±days`` window around a date, split before/on/after."""
        if days <= 0:
            raise RetrievalError(RetrievalErrorCode.INVALID_QUERY, "days must be positive")
        window_from = pivot - timedelta(days=days)
        window_to = pivot + timedelta(days=days)
        cleaned = topic.strip() if topic and topic.strip() else None

        if cleaned is not None:
            hits = self._search_or_raise(
                cleaned, date_from=window_from, date_to=window_to,
                limit=self._limits.timeline_max_hits,
            )
            entries, truncated = self._cap_entries(hits)
            truncated = truncated or len(hits) >= self._limits.timeline_max_hits
        else:
            summaries = self._index.list_notes(
                date_from=window_from, date_to=window_to,
                include_private=False, limit=self._limits.timeline_max_hits,
            )
            entries = [
                TimelineEntry(s.source_id, s.note_date, s.title, "") for s in summaries
            ]
            truncated = len(summaries) >= self._limits.timeline_max_hits

        before = tuple(e for e in entries if e.note_date and e.note_date < pivot)
        on = tuple(e for e in entries if e.note_date == pivot)
        after = tuple(e for e in entries if e.note_date and e.note_date > pivot)
        notes_found = len(before) + len(on) + len(after)
        self._record_evidence(context.session_id, (e.source_id for e in entries))
        return BeforeAfterResponse(
            request_id=context.request_id,
            pivot=pivot,
            days=days,
            topic=cleaned,
            before=before,
            on=on,
            after=after,
            notes_found=notes_found,
            insufficient_evidence=notes_found == 0,
            truncated=truncated,
        )

    # --------------------------------------------------------------- internals

    def _search_or_raise(
        self, query: str, *, date_from: Date, date_to: Date, limit: int
    ) -> List[SearchHit]:
        try:
            return self._index.search(
                query,
                limit=limit,
                include_private=False,
                date_from=date_from,
                date_to=date_to,
            )
        except sqlite3.OperationalError as exc:
            raise RetrievalError(
                RetrievalErrorCode.INVALID_QUERY, "query could not be parsed"
            ) from exc

    def _cap_entries(self, hits: Sequence[SearchHit]) -> Tuple[List[TimelineEntry], bool]:
        """Build timeline entries, enforcing the total snippet-length cap."""
        entries: List[TimelineEntry] = []
        total = 0
        for hit in hits:
            snippet = hit.snippet[: self._limits.snippet_max_chars]
            if total + len(snippet) > self._limits.max_total_snippet_chars:
                return entries, True
            total += len(snippet)
            entries.append(
                TimelineEntry(hit.source_id, hit.note_date, hit.title, snippet)
            )
        return entries, False

    def _clamp_top_k(self, top_k: Optional[int]) -> int:
        if top_k is None:
            return self._limits.default_top_k
        return max(1, min(int(top_k), self._limits.max_top_k))

    def _record_evidence(self, session_id: str, source_ids) -> None:
        with self._lock:
            bucket = self._evidence.setdefault(session_id, set())
            bucket.update(source_ids)

    def _has_evidence(self, session_id: str, source_id: str) -> bool:
        with self._lock:
            return source_id in self._evidence.get(session_id, set())
