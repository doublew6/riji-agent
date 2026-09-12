"""Models for the journal draft -> confirm -> commit flow (architecture §5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from enum import Enum
from typing import Optional, Tuple

from riji_agent.journal.content import AI_RESULT, PERSONAL, DiscussionProvenance, render_ai_result


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
    content_type: str = PERSONAL
    provenance: Optional[DiscussionProvenance] = None

    def __post_init__(self) -> None:
        if self.content_type not in {PERSONAL, AI_RESULT}:
            raise ValueError("draft_content_type_invalid")
        if (self.content_type == AI_RESULT) != (self.provenance is not None):
            raise ValueError("draft_content_provenance_invalid")

    @property
    def journal_text(self) -> str:
        if self.provenance is not None:
            return render_ai_result(self.content, self.provenance)
        return self.content


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


@dataclass(frozen=True)
class CommitVerification:
    draft_id: str
    source_id: str
    target_date: Date
    verified: bool
    repaired: bool = False
