from __future__ import annotations

from pathlib import Path

from riji_agent.config import Settings
from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.types import AssistantTurn, LLMProvider, ToolCall


def test_models_package_is_the_provider_contract_entrypoint() -> None:
    assert DeepSeekProvider.__name__ == "DeepSeekProvider"
    assert AssistantTurn(content="ok").content == "ok"
    assert ToolCall(id="c1", name="search_journal", arguments="{}").name == "search_journal"
    assert hasattr(LLMProvider, "complete")


def test_default_model_provider_is_deepseek(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    settings = Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal_root),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
    )

    assert settings.model_provider == "deepseek"


def test_model_provider_can_be_set_explicitly(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    settings = Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal_root),
        DEEPSEEK_API_KEY="secret",
        RIJI_MODEL_PROVIDER="deepseek",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
    )

    assert settings.model_provider == "deepseek"


def test_unsupported_model_provider_is_rejected(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    try:
        Settings(
            _env_file=None,
            RIJI_JOURNAL_ROOT=str(journal_root),
            DEEPSEEK_API_KEY="secret",
            RIJI_MODEL_PROVIDER="unknown",
            RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
            HERMES_SHARED_SECRET="another-secret",
        )
    except ValueError as exc:
        assert "unsupported model provider" in str(exc)
    else:
        raise AssertionError("unsupported model provider should fail validation")


def test_provider_contract_does_not_reference_journal_or_transport_terms() -> None:
    model_files = [
        path
        for path in (Path(__file__).resolve().parents[1] / "src" / "riji_agent" / "models").glob("*.py")
        if path.name != "__init__.py"
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in model_files)

    forbidden = ("journal_root", "RIJI_JOURNAL_ROOT", "FeishuMessage", "HermesGateway")
    for phrase in forbidden:
        assert phrase not in text
