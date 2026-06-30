from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.paths import default_data_dir, default_log_dir


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_defaults_are_unchanged_xdg_style(platform: str) -> None:
    # Backward compatibility: existing Linux/macOS installs must never move.
    assert default_data_dir(platform) == Path.home() / ".local" / "share" / "riji-agent"
    assert default_log_dir(platform) == Path.home() / ".riji-agent" / "logs"


def test_windows_defaults_use_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\whit\AppData\Local")
    assert default_data_dir("win32") == Path(r"C:\Users\whit\AppData\Local") / "riji-agent"
    assert default_log_dir("win32") == Path(r"C:\Users\whit\AppData\Local") / "riji-agent" / "logs"


def test_windows_defaults_fall_back_when_localappdata_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert default_data_dir("win32") == Path.home() / "AppData" / "Local" / "riji-agent"
    assert default_log_dir("win32") == Path.home() / "AppData" / "Local" / "riji-agent" / "logs"


def test_explicit_data_dir_still_wins_in_settings(tmp_path: Path) -> None:
    # The platform default is only a fallback; RIJI_DATA_DIR overrides it.
    from riji_agent.config import Settings

    journal_root = tmp_path / "journal"
    journal_root.mkdir()
    chosen = tmp_path / "custom-data"

    settings = Settings(
        _env_file=None,
        RIJI_JOURNAL_ROOT=str(journal_root),
        RIJI_DATA_DIR=str(chosen),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
    )

    assert settings.data_dir == chosen
