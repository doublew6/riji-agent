"""Model provider registry: map a configured name to a provider factory.

This is the seam that makes the model layer pluggable. ``wiring`` asks
:func:`build_model_provider` for the provider named by ``settings.model_provider``
instead of constructing one directly, so adding a model means registering a
factory here (and exposing its config), never editing the wiring branch.

Factories receive the whole ``Settings`` but must only read model-related
fields; they never touch the journal vault, IM or transport configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, FrozenSet

from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.openai_compatible import OpenAICompatibleProvider
from riji_agent.models.types import LLMError, LLMProvider

if TYPE_CHECKING:
    from riji_agent.config import Settings

ModelFactory = Callable[["Settings"], LLMProvider]

_REGISTRY: Dict[str, ModelFactory] = {}


@dataclass(frozen=True)
class ModelProcessingTarget:
    provider: str
    destination: str
    model: str


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
        api_key=_deepseek_key(settings),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
    )


def _deepseek_key(settings: "Settings") -> str:
    api_key = settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else ""
    if not api_key:
        raise LLMError("DeepSeek API key is required for the selected provider")
    return api_key


def _build_codex(settings: "Settings", purpose: str = "chat") -> LLMProvider:
    from riji_agent.models.codex import CodexProvider

    return CodexProvider(
        binary=settings.codex_bin,
        model=settings.memory_codex_model if purpose == "memory" else settings.codex_model,
        timeout_seconds=settings.codex_timeout_seconds,
        purpose=purpose,
        home=settings.codex_home or settings.data_dir / "codex",
        proxy_url=settings.codex_proxy_url.get_secret_value() if settings.codex_proxy_url else None,
    )


def build_memory_model_provider(settings: "Settings") -> LLMProvider:
    """Select one extraction/relationship/organization provider without fallback."""
    if settings.memory_model_provider == "codex":
        return _build_codex(settings, "memory")
    if settings.memory_model_provider == "deepseek":
        return DeepSeekProvider(
            api_key=_deepseek_key(settings),
            base_url=settings.deepseek_base_url,
            model="deepseek-chat",
        )
    raise LLMError("unsupported memory model provider")


def model_processing_target(settings: "Settings", purpose: str) -> ModelProcessingTarget:
    """Expose the configured data recipient for consent and safe diagnostics."""
    if purpose not in {"chat", "memory"}:
        raise ValueError("unsupported model purpose")
    provider = settings.memory_model_provider if purpose == "memory" else settings.model_provider
    if provider == "codex":
        model = settings.memory_codex_model if purpose == "memory" else settings.codex_model
        return ModelProcessingTarget(provider, "https://chatgpt.com", model)
    if provider == "deepseek":
        model = "deepseek-chat" if purpose == "memory" else settings.deepseek_model
        return ModelProcessingTarget(provider, settings.deepseek_base_url, model)
    return ModelProcessingTarget(provider, settings.model_base_url, settings.model_name)


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
register_model_provider("codex", _build_codex)
