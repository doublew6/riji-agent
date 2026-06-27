"""Backward-compatible import path for model provider adapters."""

from riji_agent.models import AssistantTurn, DeepSeekProvider, LLMError, LLMProvider, ToolCall

__all__ = [
    "AssistantTurn",
    "LLMError",
    "LLMProvider",
    "ToolCall",
    "DeepSeekProvider",
]
