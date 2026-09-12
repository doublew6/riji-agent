"""Data models for shared memory, persona-private candidates and sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional


class CandidateStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MemoryCandidate:
    """A persona-private memory candidate; not shared until confirmed."""

    id: int
    user_id: str
    persona_id: str
    content: str
    status: CandidateStatus
    created_at: str


@dataclass(frozen=True)
class ConfirmedMemory:
    """A user-confirmed long-term memory, shared across that user's personas."""

    id: int
    user_id: str
    content: str
    source_candidate_id: Optional[int]
    created_at: str


@dataclass(frozen=True)
class SessionMessage:
    """One chat message in a persona-private session history."""

    role: str
    content: str
    created_at: str
    content_type: str = "conversation"


class MemoryScope(str, Enum):
    SHARED = "shared"
    PERSONA = "persona"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class LongTermMemory:
    """One backend-independent long-term memory record."""

    id: str
    content: str
    user_id: str
    scope: MemoryScope
    persona_id: Optional[str]
    status: MemoryStatus
    created_at: Optional[str]
    updated_at: Optional[str]
    metadata: Mapping[str, Any]
    score: Optional[float] = None


@dataclass(frozen=True)
class MemoryHistoryEntry:
    """A normalized history item returned by a long-term-memory backend."""

    event: str
    created_at: Optional[str]
    before: Optional[str]
    after: Optional[str]


class CaptureJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class CaptureJob:
    id: int
    source_request_id: str
    user_id: str
    persona_id: str
    session_id: str
    content: Optional[str]
    status: CaptureJobStatus
    attempts: int
    next_attempt_at: str
    error_code: Optional[str]
    created_at: str
    updated_at: str
    source_message_id: Optional[int] = None
    source_created_at: Optional[str] = None
    extracted_json: Optional[str] = None


@dataclass(frozen=True)
class HistoricalMessage:
    """A user-authored source retained locally for capture and recall."""

    id: int
    session_id: str
    user_id: str
    persona_id: str
    content: str
    created_at: str


@dataclass(frozen=True)
class MemoryChange:
    id: int
    memory_id: str
    user_id: str
    persona_id: Optional[str]
    scope: MemoryScope
    action: str
    before: Optional[str]
    after: Optional[str]
    source_request_id: Optional[str]
    created_at: str


@dataclass(frozen=True)
class NewMemoryChange:
    """Append-only audit payload before SQLite assigns identity and time."""

    memory_id: str
    user_id: str
    persona_id: Optional[str]
    scope: MemoryScope
    action: str
    before: Optional[str]
    after: Optional[str]
    source_request_id: Optional[str] = None


def session_key(user_id: str, persona_id: str, chat_id: str) -> str:
    """Per architecture §3: history is keyed by user + persona + chat."""
    return f"{user_id}:{persona_id}:{chat_id}"
