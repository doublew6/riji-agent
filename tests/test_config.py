from pathlib import Path

import pytest

from riji_agent.config import ConfigurationError, Settings, load_settings


def test_settings_normalizes_paths_and_creates_private_data_directory(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()
    data_dir = tmp_path / "runtime"

    settings = Settings(
        _env_file=None,
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
    assert settings.feishu_voice_reply_mode == "off"
    assert settings.tts_provider == "macos_say"
    assert data_dir.is_dir()


def test_data_directory_cannot_be_inside_journal_root(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    with pytest.raises(ValueError, match="outside the journal root"):
        Settings(
            _env_file=None,
            RIJI_JOURNAL_ROOT=str(journal_root),
            RIJI_DATA_DIR=str(journal_root / "runtime"),
            DEEPSEEK_API_KEY="secret",
            RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
            HERMES_SHARED_SECRET="another-secret",
        )


def test_deepseek_base_url_rejects_cleartext_http(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    with pytest.raises(ValueError, match="HTTPS"):
        Settings(
            _env_file=None,
            RIJI_JOURNAL_ROOT=str(journal_root),
            RIJI_DATA_DIR=str(tmp_path / "runtime"),
            DEEPSEEK_API_KEY="secret",
            DEEPSEEK_BASE_URL="http://api.deepseek.com",
            RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
            HERMES_SHARED_SECRET="another-secret",
        )


def test_voice_reply_mode_accepts_text_and_voice(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    settings = Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal_root),
        RIJI_DATA_DIR=str(tmp_path / "runtime"),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
        RIJI_FEISHU_VOICE_REPLY_MODE="text_and_voice",
        RIJI_TTS_MAX_CHARS=300,
    )

    assert settings.feishu_voice_reply_mode == "text_and_voice"
    assert settings.tts_max_chars == 300


def test_settings_accepts_melotts_provider(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    settings = Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal_root),
        RIJI_DATA_DIR=str(tmp_path / "runtime"),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
        RIJI_TTS_PROVIDER="melotts",
        RIJI_TTS_LANGUAGE="zh",
        RIJI_TTS_DEVICE="cpu",
        RIJI_TTS_SPEED=1.1,
    )

    assert settings.tts_provider == "melotts"
    assert settings.tts_language == "zh"
    assert settings.tts_device == "cpu"
    assert settings.tts_speed == 1.1


def test_voice_reply_mode_rejects_unknown_value(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()

    with pytest.raises(ValueError, match="voice reply mode"):
        Settings(
            _env_file=None,
            RIJI_JOURNAL_ROOT=str(journal_root),
            RIJI_DATA_DIR=str(tmp_path / "runtime"),
            DEEPSEEK_API_KEY="secret",
            RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
            HERMES_SHARED_SECRET="another-secret",
            RIJI_FEISHU_VOICE_REPLY_MODE="always",
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

    settings = Settings(_env_file=None)

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
