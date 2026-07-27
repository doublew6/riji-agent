"""Conservative local cleanup for diary draft content."""

from __future__ import annotations

import re

_COMMAND_PREFIXES = (
    "帮我记录一下",
    "帮我记录",
    "帮我记一下",
    "帮我记",
    "在日记里记录一下",
    "在日记里记录",
    "记录一下",
    "记一下",
)
_LEADING_FILLERS = ("一下", "就是", "那个", "嗯", "呃")
_LEADING_PUNCTUATION = " ：:，,。.！!；;、\n\t "
_SPACING_RE = re.compile(r"[ \t]{2,}")
_OUTLINE_MARKER_RE = re.compile(r"^\s*(?:(?:\d+|[A-Za-z])[.)、]\s+|[-*•]\s+)")
_EXPLICIT_LIST_REQUESTS = (
    "分条",
    "列表",
    "逐条",
    "按点",
    "保留格式",
    "保持格式",
    "不要合并",
)


def polish_draft_content(content: str) -> str:
    """Lightly clean command residue and filler without changing facts."""
    text = _SPACING_RE.sub(" ", content.strip())
    text = _strip_prefixes(text, _COMMAND_PREFIXES)
    text = _strip_prefixes(text, _LEADING_FILLERS)
    text = _collapse_incidental_outline(text)
    return text.strip()


def _collapse_incidental_outline(text: str) -> str:
    if "\n" not in text or _requests_list_format(text):
        return text

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return text

    cleaned = [_OUTLINE_MARKER_RE.sub("", line).strip() for line in lines]
    cleaned = [line for line in cleaned if line]
    if len(cleaned) < 2:
        return text
    return _join_as_paragraph(cleaned)


def _requests_list_format(text: str) -> bool:
    return any(keyword in text for keyword in _EXPLICIT_LIST_REQUESTS)


def _join_as_paragraph(parts: list[str]) -> str:
    paragraph = ""
    for part in parts:
        if not paragraph:
            paragraph = part
            continue
        if _should_join_without_space(paragraph[-1], part[0]):
            paragraph += part
        else:
            paragraph += " " + part
    return paragraph


def _should_join_without_space(left: str, right: str) -> bool:
    if left in "：:（(《“‘" or right in "，,。；;：:、）)》”’":
        return True
    if _is_cjk(left) or _is_cjk(right):
        return True
    return False


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _strip_prefixes(text: str, prefixes: tuple[str, ...]) -> str:
    changed = True
    while changed:
        changed = False
        text = text.lstrip(_LEADING_PUNCTUATION)
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix) :].lstrip(_LEADING_PUNCTUATION)
                changed = True
                break
    return text
