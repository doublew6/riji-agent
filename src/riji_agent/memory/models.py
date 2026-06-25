"""Data models for shared memory, persona-private candidates and sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


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


def session_key(user_id: str, persona_id: str, chat_id: str) -> str:
    """Per architecture §3: history is keyed by user + persona + chat."""
    return f"{user_id}:{persona_id}:{chat_id}"
