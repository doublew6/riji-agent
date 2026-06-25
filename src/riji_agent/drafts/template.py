"""Template instantiation and heading-anchored appending.

Headings are the only anchor. Appending never rewrites existing text: a bullet
is inserted at the end of the target section's block. If the section heading is
absent, the caller is expected to refuse the commit rather than guess.
"""

from __future__ import annotations

from datetime import date as Date
from typing import List, Optional

from riji_agent.drafts.errors import DraftError, DraftErrorCode


def instantiate_daily(template_text: str, target_date: Date) -> str:
    """Render a new daily note from the template for ``target_date``."""
    iso = target_date.isoformat()
    return template_text.replace("{{date}}", iso).replace("{{title}}", iso)


def _heading_level(line: str) -> Optional[int]:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    return len(stripped) - len(stripped.lstrip("#"))


def _find_section(lines: List[str], section: str) -> int:
    target = section.strip()
    fallback = None
    for i, line in enumerate(lines):
        level = _heading_level(line)
        if level is None:
            continue
        heading = line.strip()[level:].strip()
        if heading == target:
            return i
        if fallback is None and target and target in heading:
            fallback = i
    if fallback is not None:
        return fallback
    raise DraftError(DraftErrorCode.SECTION_NOT_FOUND, f"section not found: {section}")


def append_to_section(text: str, section: str, content: str) -> str:
    """Insert ``- content`` at the end of ``section``'s block, leaving the rest intact."""
    lines = text.split("\n")
    heading_idx = _find_section(lines, section)
    heading_level = _heading_level(lines[heading_idx])

    end = len(lines)
    for j in range(heading_idx + 1, len(lines)):
        level = _heading_level(lines[j])
        if level is not None and level <= heading_level:
            end = j
            break

    insert_at = end
    while insert_at - 1 > heading_idx and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    lines.insert(insert_at, f"- {content}")
    return "\n".join(lines)
