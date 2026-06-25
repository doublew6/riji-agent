"""Provider-agnostic types for chat completion with tool calling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Sequence, Tuple


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model


@dataclass(frozen=True)
class AssistantTurn:
    """One assistant response: either free text or tool calls."""

    content: Optional[str]
    tool_calls: Tuple[ToolCall, ...] = ()


class LLMError(Exception):
    """A provider error that never contains credentials or request bodies."""


class LLMProvider(Protocol):
    """Minimal interface the agent loop depends on (keeps it testable)."""

    def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Sequence[Dict[str, Any]],
    ) -> AssistantTurn:
        ...
