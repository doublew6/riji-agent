"""Last-moment authorization and safe retry classification for model requests."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from riji_agent.models.types import AssistantTurn, LLMError, LLMProvider


def complete_guarded(
    provider: LLMProvider,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    before_send: Callable[[], None],
) -> AssistantTurn:
    guarded = getattr(provider, "complete_with_guard", None)
    if guarded is not None:
        return guarded(messages, tools, before_send=before_send)
    before_send()
    return provider.complete(messages, tools)


def complete_json_guarded(
    provider: LLMProvider,
    messages: Sequence[dict[str, Any]],
    schema: dict[str, Any],
    before_send: Callable[[], None],
) -> AssistantTurn:
    """Use a provider's structured output without retrying a failed request."""
    structured = getattr(provider, "complete_json_with_guard", None)
    if callable(structured):
        return structured(messages, schema, before_send=before_send)
    return complete_guarded(provider, messages, [], before_send)


def deferred_model_error(error: Exception) -> tuple[str, int] | None:
    """Allow only known safe provider codes; never serialize arbitrary errors."""
    delays = {"codex_quota_exhausted": 900, "codex_login_required": 300}
    if isinstance(error, LLMError) and str(error) in delays:
        return str(error), delays[str(error)]
    return None
