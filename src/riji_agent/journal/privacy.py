"""Local source exclusions shared by indexing and journal memory extraction."""

from __future__ import annotations

import re

PERMISSIONS = {"none", "local", "cloud"}
_MARKER = re.compile(r"<!--\s*(/riji-memory|riji-memory\s*:\s*([^>]*?))\s*-->", re.I)


def source_permission(frontmatter: dict) -> str:
    if frontmatter.get("private") is True:
        return "none"
    value = frontmatter.get("memory", "cloud")
    return value if isinstance(value, str) and value in PERMISSIONS else "none"


def cloud_body(body: str) -> str:
    """Blank restricted spans, preserving line numbers; malformed blocks fail closed."""
    output, stack, cursor = [], [], 0
    for match in _MARKER.finditer(body):
        segment = body[cursor:match.start()]
        output.append(_blank(segment) if stack else segment)
        if match[1].lower() == "/riji-memory":
            if not stack:
                return _blank(body)
            stack.pop()
        else:
            # Inline permission markers only restrict; they never override a parent.
            stack.append(match[2].strip().lower())
        output.append(_blank(match[0]))
        cursor = match.end()
    remaining = body[cursor:]
    output.append(_blank(remaining) if stack else remaining)
    return "".join(output)


def _blank(value: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in value)
