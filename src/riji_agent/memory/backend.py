"""Backend contract for authoritative Agent long-term memory."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, Sequence

from riji_agent.memory.models import LongTermMemory, MemoryHistoryEntry, MemoryScope


class LongTermMemoryBackend(Protocol):
    def health(self) -> bool:
        ...

    def search(
        self,
        query: str,
        *,
        user_id: str,
        scope: MemoryScope,
        persona_id: Optional[str] = None,
        limit: int = 8,
    ) -> Sequence[LongTermMemory]:
        ...

    def list_memories(
        self,
        *,
        user_id: str,
        persona_id: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 1000,
    ) -> Sequence[LongTermMemory]:
        ...

    def get(self, memory_id: str) -> LongTermMemory:
        ...

    def add(
        self,
        content: str,
        *,
        user_id: str,
        scope: MemoryScope,
        persona_id: Optional[str],
        metadata: Mapping[str, Any],
    ) -> Sequence[LongTermMemory]:
        ...

    def update(
        self,
        memory_id: str,
        *,
        content: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> LongTermMemory:
        ...

    def delete(self, memory_id: str) -> None:
        ...

    def history(self, memory_id: str) -> Sequence[MemoryHistoryEntry]:
        ...


class MemoryBackendError(RuntimeError):
    """Sanitized backend failure; never includes content or credentials."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
