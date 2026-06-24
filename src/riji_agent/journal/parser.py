"""Read-only Markdown + frontmatter parsing for journal notes.

The parser only reads files; it never copies or mutates the source vault.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import yaml

from riji_agent.journal.models import NoteKind, ParsedNote

# Only notes living directly under these vault folders are indexed.
_KIND_FOLDERS = {kind.value: kind for kind in NoteKind}
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class JournalParseError(ValueError):
    """Raised when a file under a journal folder cannot be parsed safely."""


def iter_note_files(journal_root: Path) -> Iterator[Path]:
    """Yield every Markdown note under the recognised period folders.

    ``templates`` and any other folders are ignored; the vault is only read.
    """
    for kind_folder in _KIND_FOLDERS:
        folder = journal_root / kind_folder
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            if path.is_file():
                yield path


def build_source_id(path: Path, journal_root: Path) -> str:
    """Return the stable wikilink target, e.g. ``riji/daily/2026-06-24``."""
    relative = path.relative_to(journal_root).with_suffix("")
    return "riji/" + relative.as_posix()


def _split_frontmatter(text: str) -> Tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise JournalParseError(f"invalid frontmatter: {exc}") from exc
    if not isinstance(loaded, dict):
        raise JournalParseError("frontmatter must be a mapping")
    body = text[match.end():]
    return loaded, body


def _coerce_tags(value: object) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = re.split(r"[,\s]+", value.strip())
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise JournalParseError("tags must be a string or a list")
    seen: List[str] = []
    for item in items:
        tag = item.strip().lstrip("#")
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


def _coerce_date(value: object) -> Optional[Date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    if isinstance(value, str):
        match = _DATE_IN_NAME_RE.search(value)
        if match:
            return Date.fromisoformat(match.group(1))
    return None


def _resolve_kind(path: Path, journal_root: Path) -> NoteKind:
    relative = path.relative_to(journal_root)
    top = relative.parts[0] if relative.parts else ""
    kind = _KIND_FOLDERS.get(top)
    if kind is None:
        raise JournalParseError(f"note is not under a journal folder: {relative.as_posix()}")
    return kind


def _resolve_date(frontmatter: dict, path: Path) -> Optional[Date]:
    explicit = _coerce_date(frontmatter.get("date"))
    if explicit is not None:
        return explicit
    return _coerce_date(path.stem)


def _resolve_title(frontmatter: dict, body: str, path: Path) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    heading = _H1_RE.search(body)
    if heading:
        return heading.group(1).strip()
    return path.stem


def _is_private(frontmatter: dict) -> bool:
    return frontmatter.get("private") is True


def parse_note(path: Path, journal_root: Path) -> ParsedNote:
    """Parse one Markdown note into a :class:`ParsedNote` without modifying it."""
    kind = _resolve_kind(path, journal_root)
    raw_bytes = path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8")
    frontmatter, body = _split_frontmatter(text)

    return ParsedNote(
        source_id=build_source_id(path, journal_root),
        relative_path=path.relative_to(journal_root).as_posix(),
        kind=kind,
        note_date=_resolve_date(frontmatter, path),
        title=_resolve_title(frontmatter, body, path),
        tags=_coerce_tags(frontmatter.get("tags")),
        body=body.strip(),
        private=_is_private(frontmatter),
        content_hash=content_hash,
    )
