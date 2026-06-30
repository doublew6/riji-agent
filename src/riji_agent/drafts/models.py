"""Models for the journal draft -> confirm -> commit flow (architecture §5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from enum import Enum
from typing import Optional, Tuple


class DraftStatus(str, Enum):
    AWAITING = "awaiting_confirmation"
    COMMITTING = "committing"  # transient: claimed by one writer, file not yet written
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class DraftOperation:
    """A single append intent: add ``content`` as a bullet under ``section``."""

    section: str
    content: str


@dataclass(frozen=True)
class Draft:
    draft_id: str
    user_id: str
    session_id: str
    persona_id: str
    target_date: Date
    operations: Tuple[DraftOperation, ...]
    token: str
    status: DraftStatus
    created_at: str
    expires_at: str
    source_id: Optional[str] = None
    after_hash: Optional[str] = None


@dataclass(frozen=True)
class DraftPreview:
    draft_id: str
    target_date: Date
    operations: Tuple[DraftOperation, ...]
    token: str
    expires_at: str
    preview_text: str


@dataclass(frozen=True)
class CommitResult:
    draft_id: str
    source_id: str
    target_date: Date
    sections: Tuple[str, ...]
    after_hash: str
    new_file: bool
