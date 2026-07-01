"""Bridge between model tool calls and the local retrieval service.

Only the five registered retrieval tools can be invoked here; an unknown tool
name returns a structured error instead of executing anything, so the model can
never reach the filesystem, the source vault or unregistered behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as Date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from riji_agent.drafts.errors import DraftError
from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.service import DraftService
from riji_agent.journal.models import NoteKind
from riji_agent.retrieval.errors import RetrievalError
from riji_agent.retrieval.models import Granularity, ToolContext
from riji_agent.retrieval.schemas import TOOL_DEFINITIONS
from riji_agent.retrieval.service import RetrievalService
from riji_agent.timezone import local_journal_timezone
from riji_agent.yangming.store import YangmingKB

_YANGMING_SNIPPET_MAX = 400

# Write tool the model may only *propose*; committing is user-driven.
DRAFT_DAILY_ENTRY_DEF: Dict[str, Any] = {
    "name": "draft_daily_entry",
    "description": (
        "Propose a journal entry for the user to confirm. Does NOT write the "
        "file; returns a preview the user must approve with 「确认保存」."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target_date": {"type": "string", "format": "date"},
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section": {"type": "string", "description": "Target heading, e.g. 🌆 Evening"},
                        "content": {"type": "string"},
                    },
                    "required": ["section", "content"],
                },
            },
        },
        "required": ["operations"],
    },
}


SEARCH_YANGMING_DEF: Dict[str, Any] = {
    "name": "search_yangming",
    "description": (
        "Search the Wang Yangming thought knowledge base (separate from the "
        "journal). Returns verbatim quotes with citations and paraphrased "
        "interpretations separately."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
}


def _tool_spec(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        },
    }


def openai_tool_specs() -> List[Dict[str, Any]]:
    """Wrap the retrieval tool definitions in the OpenAI/DeepSeek tool shape."""
    return [_tool_spec(tool) for tool in TOOL_DEFINITIONS]


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


def _local_today() -> Date:
    return datetime.now(local_journal_timezone()).date()


def _operations_mention_today(operations: Sequence[DraftOperation]) -> bool:
    return any("今天" in operation.content for operation in operations)


def _draft_target_date(value: Optional[str], operations: Sequence[DraftOperation]) -> Optional[Date]:
    target = _date(value)
    if _operations_mention_today(operations):
        return _local_today()
    return target


class ToolRegistry:
    def __init__(
        self,
        service: RetrievalService,
        *,
        draft_service: Optional[DraftService] = None,
        yangming_kb: "Optional[YangmingKB]" = None,
    ) -> None:
        self._service = service
        self._draft_service = draft_service
        self._yangming = yangming_kb
        self._handlers: Dict[str, Callable[[ToolContext, Dict[str, Any]], ToolInvocation]] = {
            "search_journal": self._search_journal,
            "read_note": self._read_note,
            "list_periods": self._list_periods,
            "timeline": self._timeline,
            "find_before_after": self._find_before_after,
        }
        if draft_service is not None:
            self._handlers["draft_daily_entry"] = self._draft_daily_entry
        if yangming_kb is not None:
            self._handlers["search_yangming"] = self._search_yangming

    def names(self) -> set:
        return set(self._handlers)

    def tool_specs(self, allowed: "Optional[Iterable[str]]" = None) -> List[Dict[str, Any]]:
        """OpenAI/DeepSeek specs for the registry's tools, optionally filtered.

        ``allowed`` restricts to a persona's allowed tool names (e.g. only the
        Wang Yangming persona may use ``search_yangming``).
        """
        catalog = list(TOOL_DEFINITIONS) + [DRAFT_DAILY_ENTRY_DEF, SEARCH_YANGMING_DEF]
        names = set(self._handlers)
        if allowed is not None:
            names &= set(allowed)
        return [_tool_spec(tool) for tool in catalog if tool["name"] in names]

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
        except (RetrievalError, DraftError) as exc:
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

    def _draft_daily_entry(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        assert self._draft_service is not None  # registered only when present
        operations = [
            DraftOperation(section=op["section"], content=op["content"])
            for op in args["operations"]
        ]
        preview = self._draft_service.create_draft(
            user_id=context.feishu_user_id,
            session_id=context.session_id,
            persona_id=context.persona_id,
            operations=operations,
            target_date=_draft_target_date(args.get("target_date"), operations),
        )
        payload = {
            "draft_id": preview.draft_id,
            "target_date": preview.target_date.isoformat(),
            "weekday": f"{preview.target_date:%A}",
            "operations": [{"section": o.section, "content": o.content} for o in preview.operations],
            "preview": preview.preview_text,
            "expires_at": preview.expires_at,
            "awaiting_confirmation": True,
        }
        return ToolInvocation(payload)

    def _search_yangming(self, context: ToolContext, args: Dict[str, Any]) -> ToolInvocation:
        assert self._yangming is not None  # registered only when present
        top_k = max(1, min(int(args.get("top_k", 5)), 10))
        hits = self._yangming.search(args["query"], limit=top_k)

        quotes: List[Dict[str, Any]] = []
        interpretations: List[Dict[str, Any]] = []
        for hit in hits:
            item = {
                "ref": hit.ref,
                "text": hit.text[:_YANGMING_SNIPPET_MAX],
                "source": hit.source,
                "version": hit.version,
                "title": hit.title,
            }
            if hit.kind.value == "quote":
                quotes.append(item)
            else:
                interpretations.append(item)
        # Source kept distinct from the journal: no journal source_ids here.
        payload = {"corpus": "wang_yangming", "quotes": quotes, "interpretations": interpretations}
        return ToolInvocation(payload)
