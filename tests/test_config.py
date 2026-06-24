from pathlib import Path

import pytest

from riji_agent.config import ConfigurationError, Settings, load_settings


def test_settings_normalizes_paths_and_creates_private_data_directory(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()
    data_dir = tmp_path / "runtime"

    settings = Settings(
        RIJI_JOURNAL_ROOT=str(journal_root),
        RIJI_DATA_DIR=str(data_dir),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one, ou_two",
        HERMES_SHARED_SECRET="another-secret",
    )
    settings.ensure_data_directory()

    assert settings.journal_root == journal_root.resolve()
    assert settings.resolved_database_path == data_dir.resolve() / "riji-agent.sqlite3"
    assert settings.allowed_feishu_user_ids == frozenset({"ou_one", "ou_two"})
    assert data_dir.is_dir()


def test_data_directory_cannot_be_inside_journal_root(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    with pytest.raises(ValueError, match="outside the journal root"):
        Settings(
            RIJI_JOURNAL_ROOT=str(journal_root),
            RIJI_DATA_DIR=str(journal_root / "runtime"),
            DEEPSEEK_API_KEY="secret",
            RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
            HERMES_SHARED_SECRET="another-secret",
        )


def test_settings_reads_comma_separated_users_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()
    monkeypatch.setenv("RIJI_JOURNAL_ROOT", str(journal_root))
    monkeypatch.setenv("RIJI_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("RIJI_ALLOWED_FEISHU_USER_IDS", "ou_one,ou_two")
    monkeypatch.setenv("HERMES_SHARED_SECRET", "another-secret")

    settings = Settings()

    assert settings.allowed_feishu_user_ids == frozenset({"ou_one", "ou_two"})


def test_load_settings_returns_safe_error_without_sensitive_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIJI_JOURNAL_ROOT", "/does/not/exist")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-that-must-not-leak")
    monkeypatch.setenv("RIJI_ALLOWED_FEISHU_USER_IDS", "ou_one")
    monkeypatch.setenv("HERMES_SHARED_SECRET", "another-secret")

    with pytest.raises(ConfigurationError) as error:
        load_settings()

    assert "secret-that-must-not-leak" not in str(error.value)
    assert "/does/not/exist" not in str(error.value)
