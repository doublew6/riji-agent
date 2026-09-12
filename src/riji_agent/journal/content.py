"""Portable journal content provenance, kept outside model-generated prose."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

ContentType = Literal["personal_journal", "ai_discussion_result", "unknown_ai"]
AI_RESULT = "ai_discussion_result"
PERSONAL = "personal_journal"
_MARKER = re.compile(r"^[ \t]*(?:-[ \t]+)?<!--\s*(/?)riji:ai-discussion-result\b([^\n]*?)(?:-->)?[ \t]*$", re.M)
_LEGACY = re.compile(r"^(#{1,6})\s+.*(?:AI\s*导师讨论结果|导师讨论结果).*$", re.M)
_CALLOUT = re.compile(r"^[ \t]*>[ \t]*\[![^\]]+\]\s*AI\s*导师讨论结果.*$", re.M)
_LEGACY_ACTOR = re.compile(
    r"^[ \t]*(?:-[ \t]+)?(?:gentle_reviewer|blunt_coach|future_self|wang_yangming|"
    r"温柔回顾者|直率教练|未来的我|王阳明导师|主持人|synthesis|host)[：:]", re.M,
)


@dataclass(frozen=True)
class DiscussionProvenance:
    result_id: str
    problem_id: str
    discussion_id: str
    artifact_ids: tuple[str, ...]
    input_revision: int
    summary_version: int
    correction_version: int
    recorded_at: str
    mentors: tuple[str, ...]
    topic: str
    saved_date: str = ""
    edited_by_user: bool = False

    def __post_init__(self) -> None:
        if (not self.result_id or not self.problem_id or not self.discussion_id
                or not self.artifact_ids or self.input_revision < 1
                or min(self.summary_version, self.correction_version) < 0):
            raise ValueError("ai_discussion_provenance_required")
        identifiers = (self.result_id, self.problem_id, self.discussion_id) + self.artifact_ids + self.mentors
        if (len(self.artifact_ids) > 5 or not 1 <= len(self.mentors) <= 4 or len(self.topic) > 200
                or len(self.recorded_at) > 50 or len(self.saved_date) > 10 or type(self.edited_by_user) is not bool
                or any(not isinstance(value, str) or not 1 <= len(value) <= 300 for value in identifiers)
                or any(type(value) is not int for value in (self.input_revision, self.summary_version, self.correction_version))):
            raise ValueError("ai_discussion_provenance_invalid")

    @classmethod
    def from_dict(cls, value: dict) -> DiscussionProvenance:
        data = dict(value)
        data["artifact_ids"] = tuple(data["artifact_ids"])
        data["mentors"] = tuple(data["mentors"])
        return cls(**data)


@dataclass(frozen=True)
class ContentSpan:
    """Offsets refer to the accompanying body/snippet, never to hidden text."""

    start: int
    end: int
    content_type: ContentType
    provenance: DiscussionProvenance | None = None


def render_ai_result(content: str, provenance: DiscussionProvenance) -> str:
    metadata = json.dumps(asdict(provenance), ensure_ascii=True, separators=(",", ":"))
    metadata = metadata.replace("<", "\\u003c").replace(">", "\\u003e")
    title = provenance.topic.replace("\n", " ").replace("\r", " ")
    lines = [f"<!-- riji:ai-discussion-result {metadata} -->",
             "> [!note] AI 导师讨论结果",
             "> 这是 AI 讨论资料；保存不代表采纳建议，也不代表计划已经执行。",
             f"> 问题：{title}",
             f"> 讨论时间：{provenance.recorded_at}；保存日期：{provenance.saved_date or '以日记日期为准'}",
             f"> 参与导师：{'、'.join(provenance.mentors)}；用户编辑：{'是' if provenance.edited_by_user else '否'}",
             f"> 讨论：{provenance.discussion_id}；摘要版本：{provenance.summary_version}",
             f"> 完整讨论：[本地讨论档案](http://127.0.0.1:8765/admin/mentors#discussion-{provenance.problem_id})",
             ">"]
    lines.extend("> " + line for line in content.splitlines())
    lines.append("<!-- /riji:ai-discussion-result -->")
    return "\n".join(lines)


def _metadata(raw: str) -> DiscussionProvenance | None:
    try:
        text = raw.strip().removesuffix("-->").strip()
        return DiscussionProvenance.from_dict(json.loads(text))
    except (ValueError, TypeError, KeyError):
        return None


def content_spans(body: str, *, default_type: ContentType = PERSONAL) -> tuple[ContentSpan, ...]:
    """Missing/invalid closing markers remain excluded through the remaining text."""
    protected = []
    opened = None
    for match in _MARKER.finditer(body):
        if not match[1]:
            if opened is None:
                opened = (match.start(), _metadata(match[2]))
        elif opened is not None:
            start, source = opened
            protected.append(ContentSpan(start, match.end(), AI_RESULT if source else "unknown_ai", source))
            opened = None
        else:
            # A missing opening tag cannot turn its preceding body into user facts.
            protected.append(ContentSpan(0, match.end(), "unknown_ai"))
    if opened is not None:
        start, source = opened
        protected.append(ContentSpan(start, len(body), "unknown_ai", source))
    protected.extend(_legacy_spans(body, protected))
    return _cover(body, protected, default_type)


def _legacy_spans(body: str, protected: list[ContentSpan]) -> list[ContentSpan]:
    spans = []
    for match in _CALLOUT.finditer(body):
        end = match.end()
        for line in body[match.end():].splitlines(keepends=True):
            if line.strip() and not line.lstrip().startswith(">"):
                break
            end += len(line)
        spans.append(ContentSpan(match.start(), end, "unknown_ai"))
    for match in _LEGACY.finditer(body):
        level = len(match[1])
        following = re.search(r"^#{1," + str(level) + r"}\s", body[match.end():], re.M)
        end = match.end() + following.start() if following else len(body)
        spans.append(ContentSpan(match.start(), end, "unknown_ai"))
    for match in _LEGACY_ACTOR.finditer(body):
        if any(item.start <= match.start() < item.end for item in protected + spans):
            continue
        end = body.find("\n\n", match.end())
        spans.append(ContentSpan(match.start(), len(body) if end < 0 else end, "unknown_ai"))
    return spans


def _cover(body: str, protected: list[ContentSpan], default_type: ContentType) -> tuple[ContentSpan, ...]:
    result, cursor = [], 0
    for span in sorted(protected, key=lambda item: (item.start, -item.end)):
        if span.end <= cursor:
            continue
        if span.start > cursor:
            result.append(ContentSpan(cursor, span.start, default_type))
        result.append(ContentSpan(max(cursor, span.start), span.end, span.content_type, span.provenance))
        cursor = span.end
    if cursor < len(body):
        result.append(ContentSpan(cursor, len(body), default_type))
    return tuple(result)


def personal_body(body: str, spans: tuple[ContentSpan, ...] | None = None) -> str:
    parts = []
    for span in spans if spans is not None else content_spans(body):
        text = body[span.start:span.end]
        parts.append(text if span.content_type == PERSONAL else "".join("\n" if c == "\n" else " " for c in text))
    return "".join(parts)


def slice_spans(spans: tuple[ContentSpan, ...], start: int, end: int, *, prefix: int = 0) -> tuple[ContentSpan, ...]:
    return tuple(ContentSpan(max(item.start, start) - start + prefix, min(item.end, end) - start + prefix,
                             item.content_type, item.provenance)
                 for item in spans if item.start < end and item.end > start)


def source_type(spans: tuple[ContentSpan, ...]) -> str:
    values = {item.content_type for item in spans}
    return next(iter(values)) if len(values) == 1 else "mixed" if values else PERSONAL


def restore_spans(values: list[dict], body: str) -> tuple[ContentSpan, ...]:
    if not values:
        return content_spans(body)
    spans = []
    try:
        for value in values:
            data = dict(value)
            if data.get("provenance"):
                data["provenance"] = DiscussionProvenance.from_dict(data["provenance"])
            span = ContentSpan(**data)
            if span.content_type not in {PERSONAL, AI_RESULT, "unknown_ai"} or not 0 <= span.start <= span.end <= len(body):
                raise ValueError("invalid_span")
            spans.append(span)
        if spans[0].start != 0 or spans[-1].end != len(body) or any(a.end != b.start for a, b in zip(spans, spans[1:])):
            raise ValueError("incomplete_spans")
    except (ValueError, TypeError, KeyError):
        return (ContentSpan(0, len(body), "unknown_ai"),)
    return tuple(spans)
