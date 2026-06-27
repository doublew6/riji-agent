"""Model provider registry: map a configured name to a provider factory.

This is the seam that makes the model layer pluggable. ``wiring`` asks
:func:`build_model_provider` for the provider named by ``settings.model_provider``
instead of constructing one directly, so adding a model means registering a
factory here (and exposing its config), never editing the wiring branch.

Factories receive the whole ``Settings`` but must only read model-related
fields; they never touch the journal vault, IM or transport configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, FrozenSet

from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.openai_compatible import OpenAICompatibleProvider
from riji_agent.models.types import LLMError, LLMProvider

if TYPE_CHECKING:
    from riji_agent.config import Settings

ModelFactory = Callable[["Settings"], LLMProvider]

_REGISTRY: Dict[str, ModelFactory] = {}


def register_model_provider(name: str, factory: ModelFactory) -> None:
    """Register ``factory`` under a case-insensitive provider ``name``."""
    _REGISTRY[name.strip().lower()] = factory


def supported_model_providers() -> FrozenSet[str]:
    """Names accepted by ``RIJI_MODEL_PROVIDER`` (used by config validation)."""
    return frozenset(_REGISTRY)


def build_model_provider(settings: "Settings") -> LLMProvider:
    """Construct the provider selected by ``settings.model_provider``."""
    try:
        factory = _REGISTRY[settings.model_provider]
    except KeyError:
        raise LLMError("unsupported model provider") from None
    return factory(settings)


def _build_deepseek(settings: "Settings") -> LLMProvider:
    return DeepSeekProvider(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
    )


def _build_openai_compatible(settings: "Settings") -> LLMProvider:
    # Settings validation guarantees the key is present for this provider; guard
    # again so a misconfiguration fails as a safe LLMError, never a None deref.
    api_key = settings.model_api_key.get_secret_value() if settings.model_api_key else ""
    if not api_key:
        raise LLMError("model api key is required for the selected provider")
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=settings.model_base_url,
        model=settings.model_name,
        provider_label="openai",
    )


register_model_provider("deepseek", _build_deepseek)
register_model_provider("openai", _build_openai_compatible)
