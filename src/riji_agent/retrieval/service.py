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
from typing import Dict, List, Optional, Sequence, Set

from riji_agent.journal.index import JournalIndex
from riji_agent.retrieval.errors import RetrievalError, RetrievalErrorCode
from riji_agent.retrieval.models import (
    NoteResponse,
    PeriodItem,
    PeriodsResponse,
    RetrievalLimits,
    SearchResponse,
    SearchResultItem,
    ToolContext,
)
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

    # --------------------------------------------------------------- internals

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
