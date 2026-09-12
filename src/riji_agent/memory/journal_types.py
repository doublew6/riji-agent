"""Contracts for bounded, evidence-backed journal memory processing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from riji_agent.journal.content import DiscussionProvenance


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_key(value: str) -> str:
    return fingerprint(" ".join(value.casefold().split()))


@dataclass(frozen=True)
class JournalMemoryPolicy:
    root: Path
    user_id: str
    sections: tuple[str, ...]
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    segment_chars: int = 900
    source_chars: int = 4000
    daily_chars: int = 100000
    initialization_unlimited: bool = False
    file_bytes: int = 262144
    read_timeout: float = 2.0
    scan_seconds: float = 60.0
    max_attempts: int = 3
    enabled: bool = True
    settle_seconds: float = 2.0
    extraction_destination: str = "https://api.deepseek.com"
    extraction_provider: str = "deepseek"
    extraction_model: str = "deepseek-chat"
    recall_destination: str = "https://api.deepseek.com"
    recall_provider: str = "deepseek"
    recall_model: str = "deepseek-reasoner"
    mentors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        if not self.user_id or not self.sections or any(not s.strip() for s in self.sections):
            raise ValueError("journal_memory_scope_required")
        for value in (self.date_from, self.date_to):
            if value:
                date.fromisoformat(value)
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("journal_memory_invalid_dates")
        if not 100 <= self.segment_chars <= 2000:
            raise ValueError("journal_memory_invalid_segment_limit")
        if min(self.source_chars, self.daily_chars, self.file_bytes, self.max_attempts) < 1:
            raise ValueError("journal_memory_invalid_budget")
        if min(self.read_timeout, self.scan_seconds) <= 0:
            raise ValueError("journal_memory_invalid_interval")
        if self.settle_seconds < 0:
            raise ValueError("journal_memory_invalid_settle_interval")

    @property
    def scope_id(self) -> str:
        values = [str(self.root.resolve()), self.user_id, sorted(self.sections),
                  self.date_from, self.date_to, self.enabled]
        return fingerprint(json.dumps(values, ensure_ascii=False))


@dataclass(frozen=True)
class JournalEvidence:
    id: str
    source_id: str
    path: str
    version: str
    kind: str
    section: str
    line: int
    observed_at: Optional[str]
    text: str
    content_type: str = "personal_journal"
    provenance: Optional[DiscussionProvenance] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class JournalSource:
    id: str
    path: str
    version: str
    kind: str
    observed_at: Optional[str]
    status: str
    reason: Optional[str]
    evidence: tuple[JournalEvidence, ...] = ()


@dataclass(frozen=True)
class JournalCandidate:
    content: str
    kind: str
    certainty: str
    valid_from: Optional[str]
    quotes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


class JournalMemoryError(RuntimeError):
    """A public error code without journal text or filesystem paths."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
