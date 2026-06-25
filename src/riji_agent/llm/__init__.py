"""OpenAI-compatible LLM provider adapters (DeepSeek)."""

from riji_agent.llm.types import AssistantTurn, LLMError, LLMProvider, ToolCall
from riji_agent.llm.deepseek import DeepSeekProvider

__all__ = [
    "AssistantTurn",
    "LLMError",
    "LLMProvider",
    "ToolCall",
    "DeepSeekProvider",
]
