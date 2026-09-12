"""Read-only recursive discovery and explicitly scoped journal excerpts."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Iterator

from riji_agent.journal.parser import JournalParseError, parse_note, read_file_bytes
from riji_agent.journal.privacy import cloud_body
from riji_agent.journal.content import personal_body
from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.journal_types import (
    JournalEvidence, JournalMemoryError, JournalMemoryPolicy, JournalSource, fingerprint,
)

_EXCLUDED = {"templates", "assets", "attachments", "output", "node_modules"}
_KINDS = ("daily", "weekly", "monthly")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def discover_sources(policy: JournalMemoryPolicy) -> Iterator[JournalSource]:
    root = policy.root.resolve()
    if not root.is_dir():
        raise JournalMemoryError("journal_root_unavailable")
    folders = [root / kind for kind in _KINDS if (root / kind).is_dir() and not (root / kind).is_symlink()]
    if not folders:
        raise JournalMemoryError("journal_layout_unrecognized")
    for folder in folders:
        if folder.is_symlink():
            continue
        yield from _walk_folder(folder, policy)


def _walk_folder(folder: Path, policy: JournalMemoryPolicy) -> Iterator[JournalSource]:
    errors = []
    for base, directories, files in os.walk(folder, followlinks=False, onerror=errors.append):
        directories[:] = sorted(
            name for name in directories
            if not name.startswith(".") and name.casefold() not in _EXCLUDED
            and not (Path(base) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(base) / name
            if name.startswith(".") or path.suffix.lower() != ".md" or path.is_symlink():
                continue
            yield read_source(path, policy)
    if errors:
        raise JournalMemoryError("journal_directory_unreadable")


def read_source(path: Path, policy: JournalMemoryPolicy) -> JournalSource:
    root = policy.root.resolve()
    try:
        relative_path = path.absolute().relative_to(root)
        relative = relative_path.as_posix()
        if path.resolve() != root / relative or any(
                part.startswith(".") or part.casefold() in _EXCLUDED for part in relative_path.parts):
            raise ValueError("excluded")
    except (ValueError, OSError):
        raise JournalMemoryError("journal_path_outside_scope") from None
    source_id = fingerprint(policy.user_id + ":" + relative)
    kind = relative.split("/", 1)[0]
    try:
        before = path.stat()
        if kind not in _KINDS or before.st_size > policy.file_bytes:
            return JournalSource(source_id, relative, "", kind, None, "excluded", "file_limit")
        if time.time() - before.st_mtime < policy.settle_seconds:
            return JournalSource(source_id, relative, "", kind, None, "failed", "source_still_changing")
        raw = read_file_bytes(path, policy.read_timeout)
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino)
                or len(raw) > policy.file_bytes or path.resolve() != root / relative):
            raise ValueError("changed_source")
        note = parse_note(path, root, reader=lambda _: raw)
    except (OSError, UnicodeError, JournalParseError, ValueError):
        return JournalSource(source_id, relative, "", kind, None, "failed", "source_unreadable")
    observed = note.note_date.isoformat() if note.note_date else None
    reason = _exclusion(note.private, observed, policy)
    if reason:
        return JournalSource(source_id, relative, note.content_hash, kind, observed, "excluded", reason)
    source = JournalSource(source_id, relative, note.content_hash, kind, observed, "eligible", None)
    original = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    frontmatter = re.match(r"^---\n.*?\n---\n?", original, re.DOTALL)
    sanitized = cloud_body(original)
    start = sanitized.find(note.body, frontmatter.end() if frontmatter else 0)
    offset = original[:start].count("\n")
    evidence = _excerpts("\n" * offset + personal_body(note.body, note.content_spans), source, policy)
    return JournalSource(source_id, relative, note.content_hash, kind, observed,
                         "eligible" if evidence else "excluded", None if evidence else "no_selected_content", evidence)


def _exclusion(private: bool, observed: str | None, policy: JournalMemoryPolicy) -> str | None:
    if private:
        return "private"
    if (policy.date_from or policy.date_to) and observed is None:
        return "date_unknown_outside_selected_range"
    if observed and ((policy.date_from and observed < policy.date_from)
                     or (policy.date_to and observed > policy.date_to)):
        return "outside_date_range"
    return None


def _paragraphs(body: str, sections: tuple[str, ...]) -> Iterator[tuple[str, int, str]]:
    allowed = {_heading_key(value) for value in sections}
    headings: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    section = ""
    fenced = ""
    for number, line in enumerate(body.splitlines() + [""], 1):
        match = _HEADING.match(line)
        marker = _FENCE.match(line)
        if match or not line.strip() or marker:
            if pending:
                yield section, start, "\n".join(pending).strip()
                pending = []
        if marker:
            if not fenced:
                fenced = marker[1]
            elif marker[1][0] == fenced[0] and len(marker[1]) >= len(fenced):
                fenced = ""
            continue
        if match and not fenced:
            level, title = len(match[1]), match[2].strip()
            headings = [item for item in headings if item[0] < level] + [(level, title)]
            section = next((name for _, name in reversed(headings) if _heading_key(name) in allowed), "")
        elif section and line.strip() and not fenced:
            if not pending:
                start = number
            pending.append(line)


def _heading_key(value: str) -> str:
    return " ".join(value.replace("**", "").replace("__", "").split())


def _excerpts(body: str, source: JournalSource,
              policy: JournalMemoryPolicy) -> tuple[JournalEvidence, ...]:
    evidence = []
    for section, line, paragraph in _paragraphs(personal_body(body), policy.sections):
        if contains_credentials(paragraph) or len(paragraph.strip("- *>_[]")) < 6:
            continue
        for index in range(0, len(paragraph), policy.segment_chars):
            text = paragraph[index:index + policy.segment_chars]
            identity = [source.id, section, source.observed_at or "", text]
            evidence_id = fingerprint("\0".join(identity))
            evidence.append(JournalEvidence(evidence_id, source.id, source.path, source.version,
                            source.kind, section, line, source.observed_at, text))
    return tuple({item.id: item for item in evidence}.values())
