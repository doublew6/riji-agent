"""Atomic append into riji/daily/YYYY-MM-DD.md.

All section edits are computed in memory first; the file is only replaced if
every operation succeeds, via a temp file + os.replace, so a failure never
leaves a half-written note.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path
from typing import Sequence, Tuple

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.template import append_to_section, instantiate_daily
from riji_agent.journal.parser import build_source_id


@dataclass(frozen=True)
class WriteOutcome:
    path: Path
    source_id: str
    before_hash: str
    after_hash: str
    sections: Tuple[str, ...]
    new_file: bool


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit_operations(
    journal_root: Path, target_date: Date, operations: Sequence[DraftOperation]
) -> WriteOutcome:
    if not operations:
        raise DraftError(DraftErrorCode.NO_OPERATIONS, "draft has no operations")

    daily_dir = journal_root / "daily"
    path = daily_dir / f"{target_date.isoformat()}.md"

    if path.exists():
        text = path.read_text(encoding="utf-8")
        before_hash = _sha256(text)
        new_file = False
    else:
        template_path = journal_root / "templates" / "daily.md"
        if not template_path.is_file():
            raise DraftError(DraftErrorCode.TEMPLATE_NOT_FOUND, "daily template is missing")
        text = instantiate_daily(template_path.read_text(encoding="utf-8"), target_date)
        before_hash = ""
        new_file = True

    sections = []
    for operation in operations:
        text = append_to_section(text, operation.section, operation.content)  # may raise
        sections.append(operation.section)

    daily_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp-{uuid.uuid4().hex}"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)  # atomic within the same directory

    return WriteOutcome(
        path=path,
        source_id=build_source_id(path, journal_root),
        before_hash=before_hash,
        after_hash=_sha256(text),
        sections=tuple(sections),
        new_file=new_file,
    )
