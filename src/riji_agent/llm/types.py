"""Backward-compatible import path for model provider contracts."""

from riji_agent.models.types import AssistantTurn, LLMError, LLMProvider, ToolCall

__all__ = ["AssistantTurn", "LLMError", "LLMProvider", "ToolCall"]
