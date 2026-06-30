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


def polish_draft_content(content: str) -> str:
    """Lightly clean command residue and filler without changing facts."""
    text = _SPACING_RE.sub(" ", content.strip())
    text = _strip_prefixes(text, _COMMAND_PREFIXES)
    text = _strip_prefixes(text, _LEADING_FILLERS)
    return text.strip()


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
