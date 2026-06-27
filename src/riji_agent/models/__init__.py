"""Model provider adapters and provider-agnostic chat completion contracts."""

from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.openai_compatible import OpenAICompatibleProvider
from riji_agent.models.registry import (
    build_model_provider,
    register_model_provider,
    supported_model_providers,
)
from riji_agent.models.types import AssistantTurn, LLMError, LLMProvider, ToolCall

__all__ = [
    "AssistantTurn",
    "DeepSeekProvider",
    "OpenAICompatibleProvider",
    "LLMError",
    "LLMProvider",
    "ToolCall",
    "build_model_provider",
    "register_model_provider",
    "supported_model_providers",
]
