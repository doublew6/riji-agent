"""Bridge between model tool calls and the local retrieval service.

Only the five registered retrieval tools can be invoked here; an unknown tool
name returns a structured error instead of executing anything, so the model can
never reach the filesystem, the source vault or unregistered behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as Date
from typing import Any, Callable, Dict, List, Optional, Tuple

from riji_agent.journal.models import NoteKind
from riji_agent.retrieval.errors import RetrievalError
from riji_agent.retrieval.models import Granularity, ToolContext
from riji_agent.retrieval.schemas import TOOL_DEFINITIONS
from riji_agent.retrieval.service import RetrievalService


def openai_tool_specs() -> List[Dict[str, Any]]:
    """Wrap the retrieval tool definitions in the OpenAI/DeepSeek tool shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in TOOL_DEFINITIONS
    ]


@dataclass(frozen=True)
class ToolInvocation:
    """Outcome of one tool call: payload handed back to the model + audit data."""

    payload: Dict[str, Any]
    source_ids: Tuple[str, ...] = ()
    ok: bool = True
    error: Optional[str] = None


def _date(value: Optional[str]) -> Optional[Date]:
    if value is None:
        return None
    return Date.fromisoformat(value)


class ToolRegistry:
    def __init__(self, service: RetrievalService) -> None:
        self._service = service
        self._handlers: Dict[str, Callable[[ToolContext, Dict[str, Any]], ToolInvocation]] = {
            "search_journal": self._search_journal,
            "read_note": self._read_note,
            "list_periods": self._list_periods,
            "timeline": self._timeline,
            "find_before_after": self._find_before_after,
        }

    def names(self) -> set:
        return set(self._handlers)

    def invoke(self, context: ToolContext, name: str, arguments_json: str) -> ToolInvocation:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolInvocation(
                {"error": "unknown_tool", "message": f"no such tool: {name}"},
                ok=False,
                error="unknown_tool",
            )
        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError:
            return ToolInvocation(
                {"error": "invalid_arguments", "message": "arguments must be valid JSON"},
                ok=False,
                error="invalid_arguments",
            )
        if not isinstance(args, dict):
            return ToolInvocation(
                {"error": "invalid_arguments", "message": "arguments must be an object"},
                ok=False,
                error="invalid_arguments",
            )
        try:
            return handler(context, args)
        except RetrievalError as exc:
            return ToolInvocation(exc.to_dict(), ok=False, error=exc.code.value)
        except (KeyError, ValueError) as exc:
            return ToolInvocation(
                {"error": "invalid_arguments", "message": str(exc)},
                ok=False,
                error="invalid_arguments",
            )

    # ------------------------------------------------------------- handlers

    def _search_journal(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        response = self._service.search_journal(
            context,
            args["query"],
            date_from=_date(args.get("date_from")),
            date_to=_date(args.get("date_to")),
            tags=args.get("tags"),
            top_k=args.get("top_k"),
        )
        items = [
            {
                "source_id": item.source_id,
                "title": item.title,
                "date": item.note_date.isoformat() if item.note_date else None,
                "snippet": item.snippet,
            }
            for item in response.items
        ]
        return ToolInvocation(
            {"items": items, "truncated": response.truncated},
            source_ids=tuple(item.source_id for item in response.items),
        )

    def _read_note(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        response = self._service.read_note(context, args["source_id"])
        payload = {
            "source_id": response.source_id,
            "title": response.title,
            "date": response.note_date.isoformat() if response.note_date else None,
            "body": response.body,
            "truncated": response.truncated,
        }
        return ToolInvocation(payload, source_ids=(response.source_id,))

    def _list_periods(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        kind = NoteKind(args["kind"]) if args.get("kind") else None
        response = self._service.list_periods(
            context, kind=kind, date_from=_date(args.get("date_from")), date_to=_date(args.get("date_to"))
        )
        items = [
            {
                "source_id": item.source_id,
                "kind": item.kind.value,
                "date": item.note_date.isoformat() if item.note_date else None,
                "title": item.title,
            }
            for item in response.items
        ]
        return ToolInvocation(
            {"items": items}, source_ids=tuple(item.source_id for item in response.items)
        )

    def _timeline(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        response = self._service.timeline(
            context,
            args["topic"],
            _date(args["date_from"]),
            _date(args["date_to"]),
            Granularity(args.get("granularity", "day")),
        )
        buckets = []
        source_ids: List[str] = []
        for bucket in response.buckets:
            entries = []
            for entry in bucket.entries:
                source_ids.append(entry.source_id)
                entries.append(
                    {
                        "source_id": entry.source_id,
                        "date": entry.note_date.isoformat() if entry.note_date else None,
                        "title": entry.title,
                        "snippet": entry.snippet,
                    }
                )
            buckets.append({"period": bucket.period, "entries": entries})
        payload = {
            "buckets": buckets,
            "empty_periods": list(response.empty_periods),
            "notes_found": response.notes_found,
            "insufficient_evidence": response.insufficient_evidence,
            "truncated": response.truncated,
        }
        return ToolInvocation(payload, source_ids=tuple(source_ids))

    def _find_before_after(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        response = self._service.find_before_after(
            context, _date(args["date"]), int(args["days"]), topic=args.get("topic")
        )

        def render(entries) -> List[Dict[str, Any]]:
            rendered = []
            for entry in entries:
                rendered.append(
                    {
                        "source_id": entry.source_id,
                        "date": entry.note_date.isoformat() if entry.note_date else None,
                        "title": entry.title,
                        "snippet": entry.snippet,
                    }
                )
            return rendered

        source_ids = tuple(
            e.source_id for e in (*response.before, *response.on, *response.after)
        )
        payload = {
            "before": render(response.before),
            "on": render(response.on),
            "after": render(response.after),
            "notes_found": response.notes_found,
            "insufficient_evidence": response.insufficient_evidence,
            "truncated": response.truncated,
        }
        return ToolInvocation(payload, source_ids=source_ids)
