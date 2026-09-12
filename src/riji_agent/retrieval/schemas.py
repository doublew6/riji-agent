"""Versioned JSON Schema and tool definitions for the retrieval tools.

These definitions are what Hermes registers with DeepSeek for tool calling
(MVP-05). The schemas intentionally expose ``source_id`` only — never file
paths — so the model cannot request arbitrary filesystem reads.
"""

from __future__ import annotations

from typing import Dict, List

SCHEMA_VERSION = "1"

_DATE = {"type": "string", "format": "date", "description": "ISO date YYYY-MM-DD"}

SEARCH_JOURNAL_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "minLength": 1, "description": "Free-text query"},
        "date_from": _DATE,
        "date_to": _DATE,
        "tags": {"type": "array", "items": {"type": "string"}},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["query"],
}

READ_NOTE_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_id": {
            "type": "string",
            "pattern": r"^riji/(daily|weekly|monthly)/.+",
            "description": "Stable source id from a prior search result",
        }
    },
    "required": ["source_id"],
}

LIST_PERIODS_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
        "date_from": _DATE,
        "date_to": _DATE,
    },
    "required": [],
}

TIMELINE_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topic": {"type": "string", "minLength": 1},
        "date_from": _DATE,
        "date_to": _DATE,
        "granularity": {"type": "string", "enum": ["day", "week", "month"], "default": "day"},
    },
    "required": ["topic", "date_from", "date_to"],
}

FIND_BEFORE_AFTER_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "date": {**_DATE, "description": "Query anchor for comparing journal record dates, not an asserted event date."},
        "days": {"type": "integer", "minimum": 1, "description": "Half-window in days"},
        "topic": {"type": "string"},
    },
    "required": ["date", "days"],
}

TOOL_DEFINITIONS: List[Dict] = [
    {
        "name": "search_journal",
        "description": (
            "Search the local journal. Returns minimal snippets with stable "
            "source ids; never returns private notes. Only bounded query matches: "
            "a miss does not establish missing entries or absent events. See evidence_scope."
        ),
        "parameters": SEARCH_JOURNAL_SCHEMA,
    },
    {
        "name": "read_note",
        "description": (
            "Read a note previously surfaced by search_journal, by its source id. "
            "Only its permitted bounded body is returned, not an exhaustive record of events."
        ),
        "parameters": READ_NOTE_SCHEMA,
    },
    {
        "name": "list_periods",
        "description": "List bounded visible journal metadata by kind and date range; omissions do not prove absent entries.",
        "parameters": LIST_PERIODS_SCHEMA,
    },
    {
        "name": "timeline",
        "description": (
            "Group journal evidence about a topic by journal record date into day/week/month buckets. "
            "query_window describes the requested record-date range; event dates require content evidence. "
            "empty_periods are gaps in returned topic matches, not missing entries or absent events."
        ),
        "parameters": TIMELINE_SCHEMA,
    },
    {
        "name": "find_before_after",
        "description": (
            "Find journal entries within a +/- days window around a query anchor, "
            "split into before/on/after by journal record date; optionally filtered by topic. "
            "query_anchor is not an asserted event date; event order requires content evidence. Results are bounded; "
            "empty groups do not prove missing entries or absent events."
        ),
        "parameters": FIND_BEFORE_AFTER_SCHEMA,
    },
]
