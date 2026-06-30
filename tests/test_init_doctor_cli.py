from __future__ import annotations

from pathlib import Path

from riji_agent.config_cli import (
    DEFAULT_PRESET,
    DoctorResult,
    run_doctor,
    write_init_env,
)


def test_init_writes_default_stack_env_without_real_secret_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    journal_root = tmp_path / "journal"
    data_dir = tmp_path / "data"

    write_init_env(
        env_file,
        preset=DEFAULT_PRESET,
        journal_root=journal_root,
        data_dir=data_dir,
        deepseek_api_key="replace-me",
        feishu_user_ids=("ou_replace_me",),
        hermes_shared_secret=None,
        force=False,
    )

    text = env_file.read_text(encoding="utf-8")
    assert "RIJI_IM_PROVIDER=feishu" in text
    assert "RIJI_AGENT_RUNTIME=hermes" in text
    assert "RIJI_MODEL_PROVIDER=deepseek" in text
    assert f"RIJI_JOURNAL_ROOT={journal_root}" in text
    assert f"RIJI_DATA_DIR={data_dir}" in text
    assert "DEEPSEEK_API_KEY=replace-me" in text
    assert "RIJI_ALLOWED_FEISHU_USER_IDS=ou_replace_me" in text
    assert "HERMES_SHARED_SECRET=replace-with" not in text


def test_init_refuses_to_overwrite_existing_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=true\n", encoding="utf-8")

    try:
        write_init_env(env_file, preset=DEFAULT_PRESET, force=False)
    except FileExistsError:
        pass
    else:
        raise AssertionError("init should not overwrite an existing env file")

    assert env_file.read_text(encoding="utf-8") == "EXISTING=true\n"


def test_doctor_returns_safe_failure_without_leaking_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret = "secret-that-must-not-leak"
    missing_path = tmp_path / "missing-journal"
    env_file.write_text(
        "\n".join(
            [
                f"RIJI_JOURNAL_ROOT={missing_path}",
                f"DEEPSEEK_API_KEY={secret}",
                "RIJI_ALLOWED_FEISHU_USER_IDS=ou_1",
                "HERMES_SHARED_SECRET=another-secret",
            ]
        ),
        encoding="utf-8",
    )

    result = run_doctor(env_file=env_file)

    assert isinstance(result, DoctorResult)
    assert result.ok is False
    rendered = "\n".join(result.messages)
    assert secret not in rendered
    assert str(missing_path) not in rendered
    assert "configuration: invalid" in rendered
