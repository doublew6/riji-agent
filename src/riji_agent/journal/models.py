"""Structured representation of a parsed journal note."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from enum import Enum
from typing import Optional, Tuple


class NoteKind(str, Enum):
    """The journal period a note belongs to, derived from its vault folder."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class NoteSummary:
    """Lightweight note metadata for listing, without loading the body."""

    source_id: str
    kind: NoteKind
    note_date: Optional[Date]
    title: str
    private: bool


@dataclass(frozen=True)
class ParsedNote:
    """A read-only note extracted from the vault.

    ``source_id`` is the stable Obsidian wikilink target (e.g.
    ``riji/daily/2026-06-24``) so retrieval results can always link back to the
    source. ``private`` mirrors the ``private: true`` frontmatter flag so the
    permission layer can reliably block such notes from leaving the device.
    """

    source_id: str
    relative_path: str
    kind: NoteKind
    note_date: Optional[Date]
    title: str
    tags: Tuple[str, ...]
    body: str
    private: bool
    content_hash: str
