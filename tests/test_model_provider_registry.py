from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.config import Settings
from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.openai_compatible import OpenAICompatibleProvider
from riji_agent.models.registry import (
    build_model_provider,
    supported_model_providers,
)


def _settings(tmp_path: Path, **overrides: str) -> Settings:
    journal_root = tmp_path / "journal"
    journal_root.mkdir(exist_ok=True)
    base = {
        "_env_file": None,
        "RIJI_JOURNAL_ROOT": str(journal_root),
        "DEEPSEEK_API_KEY": "secret",
        "RIJI_ALLOWED_FEISHU_USER_IDS": "ou_one",
        "HERMES_SHARED_SECRET": "another-secret",
    }
    base.update(overrides)
    return Settings(**base)


def test_registry_exposes_deepseek_and_openai() -> None:
    names = supported_model_providers()
    assert "deepseek" in names
    assert "openai" in names


def test_build_default_provider_is_deepseek(tmp_path: Path) -> None:
    provider = build_model_provider(_settings(tmp_path))
    assert isinstance(provider, DeepSeekProvider)


def test_build_openai_provider_when_selected(tmp_path: Path) -> None:
    provider = build_model_provider(
        _settings(
            tmp_path,
            RIJI_MODEL_PROVIDER="openai",
            RIJI_MODEL_API_KEY="sk-openai",
            RIJI_MODEL_NAME="gpt-4o-mini",
        )
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert not isinstance(provider, DeepSeekProvider)


def test_openai_provider_requires_api_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model api key is required"):
        _settings(tmp_path, RIJI_MODEL_PROVIDER="openai")


def test_unsupported_provider_is_rejected_at_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported model provider"):
        _settings(tmp_path, RIJI_MODEL_PROVIDER="anthropic")


def test_unsupported_im_and_runtime_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported IM provider"):
        _settings(tmp_path, RIJI_IM_PROVIDER="slack")
    with pytest.raises(ValueError, match="unsupported agent runtime"):
        _settings(tmp_path, RIJI_AGENT_RUNTIME="langgraph")
