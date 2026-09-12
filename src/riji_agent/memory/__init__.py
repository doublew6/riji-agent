"""Memory persistence: shared confirmed memories, private candidates, sessions."""

from riji_agent.memory.models import (
    CandidateStatus,
    ConfirmedMemory,
    MemoryCandidate,
    SessionMessage,
    session_key,
)
from riji_agent.memory.store import MemoryStore

__all__ = [
    "CandidateStatus",
    "ConfirmedMemory",
    "MemoryCandidate",
    "SessionMessage",
    "session_key",
    "MemoryStore",
]
"""Long-term memory backends, capture and review services."""

from riji_agent.memory.backend import LongTermMemoryBackend, MemoryBackendError
from riji_agent.memory.mem0 import Mem0Client

__all__ = ["LongTermMemoryBackend", "Mem0Client", "MemoryBackendError"]
