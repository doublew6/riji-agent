from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from riji_agent.config_cli import run_doctor
from riji_agent.service import (
    LaunchdServiceConfig,
    LaunchdServiceManager,
    ServiceStatus,
    UnsupportedServiceTargetError,
    render_launchd_plist,
)


def test_launchd_plist_contains_service_shape_without_secrets(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "riji-agent"
    workdir = tmp_path / "repo"
    log_dir = tmp_path / "logs"
    secret = "sk-secret-that-must-not-leak"
    config = LaunchdServiceConfig(
        executable=executable,
        working_directory=workdir,
        log_dir=log_dir,
    )

    data = plistlib.loads(render_launchd_plist(config))
    rendered = render_launchd_plist(config).decode("utf-8")

    assert data["Label"] == "ai.riji-agent"
    assert data["ProgramArguments"] == [str(executable), "serve"]
    assert data["WorkingDirectory"] == str(workdir)
    assert data["StandardOutPath"] == str(log_dir / "service.log")
    assert data["StandardErrorPath"] == str(log_dir / "service.error.log")
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] is True
    assert secret not in rendered
    assert "DEEPSEEK_API_KEY" not in rendered
    assert "HERMES_SHARED_SECRET" not in rendered
    assert "RIJI_JOURNAL_ROOT" not in rendered


def test_launchd_install_is_idempotent_and_updates_plist(tmp_path: Path) -> None:
    manager = LaunchdServiceManager(
        config=LaunchdServiceConfig(
            executable=tmp_path / "bin" / "riji-agent",
            working_directory=tmp_path / "repo",
            log_dir=tmp_path / "logs",
            plist_path=tmp_path / "LaunchAgents" / "ai.riji-agent.plist",
        ),
        runner=_FakeRunner(),
        platform="darwin",
        url_checker=lambda _url: True,
    )

    first = manager.install()
    second = manager.install()

    assert first.installed is True
    assert second.installed is True
    assert manager.config.plist_path.exists()
    assert manager.config.log_dir.exists()
    bootouts = [command for command in manager.runner.commands if command[:2] == ("launchctl", "bootout")]
    bootstraps = [
        command for command in manager.runner.commands if command[:2] == ("launchctl", "bootstrap")
    ]
    assert len(bootouts) == 2
    assert len(bootstraps) == 2


def test_launchd_start_stop_restart_status_and_logs_use_injected_runner(tmp_path: Path) -> None:
    runner = _FakeRunner(print_result="state = running\npid = 1234\n")
    manager = LaunchdServiceManager(
        config=LaunchdServiceConfig(
            executable=tmp_path / "bin" / "riji-agent",
            working_directory=tmp_path / "repo",
            log_dir=tmp_path / "logs",
            plist_path=tmp_path / "LaunchAgents" / "ai.riji-agent.plist",
        ),
        runner=runner,
        platform="darwin",
        url_checker=lambda _url: True,
    )
    manager.install()
    (manager.config.log_dir / "service.log").write_text("started\nready\n", encoding="utf-8")

    assert manager.start().loaded is True
    assert manager.stop().loaded is False
    assert manager.restart().loaded is True
    status = manager.status()
    logs = manager.logs(lines=1)

    assert status.running is True
    assert status.pid == 1234
    assert status.health == "ok"
    assert status.label == "ai.riji-agent"
    assert status.plist_path == manager.config.plist_path
    assert logs == "ready"


def test_uninstall_removes_only_generated_plist(tmp_path: Path) -> None:
    data_file = tmp_path / "data.sqlite3"
    env_file = tmp_path / ".env"
    log_file = tmp_path / "logs" / "service.log"
    data_file.write_text("keep", encoding="utf-8")
    env_file.write_text("keep", encoding="utf-8")
    manager = LaunchdServiceManager(
        config=LaunchdServiceConfig(
            executable=tmp_path / "bin" / "riji-agent",
            working_directory=tmp_path / "repo",
            log_dir=tmp_path / "logs",
            plist_path=tmp_path / "LaunchAgents" / "ai.riji-agent.plist",
        ),
        runner=_FakeRunner(),
        platform="darwin",
        url_checker=lambda _url: False,
    )
    manager.install()
    log_file.write_text("keep", encoding="utf-8")

    status = manager.uninstall()

    assert status.installed is False
    assert not manager.config.plist_path.exists()
    assert data_file.exists()
    assert env_file.exists()
    assert log_file.exists()


def test_launchd_refuses_unsupported_platform(tmp_path: Path) -> None:
    manager = LaunchdServiceManager(
        config=LaunchdServiceConfig(
            executable=tmp_path / "bin" / "riji-agent",
            working_directory=tmp_path / "repo",
            log_dir=tmp_path / "logs",
            plist_path=tmp_path / "LaunchAgents" / "ai.riji-agent.plist",
        ),
        runner=_FakeRunner(),
        platform="linux",
        url_checker=lambda _url: False,
    )

    with pytest.raises(UnsupportedServiceTargetError):
        manager.install()


def test_doctor_reports_service_status_when_installed(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    journal = tmp_path / "journal"
    journal.mkdir()
    env_file.write_text(
        "\n".join(
            [
                f"RIJI_JOURNAL_ROOT={journal}",
                f"RIJI_DATA_DIR={tmp_path / 'data'}",
                "DEEPSEEK_API_KEY=secret",
                "RIJI_ALLOWED_FEISHU_USER_IDS=ou_1",
                "HERMES_SHARED_SECRET=another-secret",
            ]
        ),
        encoding="utf-8",
    )

    result = run_doctor(
        env_file=env_file,
        service_status_provider=lambda: ServiceStatus(
            installed=True,
            loaded=True,
            running=True,
            pid=42,
            label="ai.riji-agent",
            plist_path=tmp_path / "LaunchAgents" / "ai.riji-agent.plist",
            health="ok",
        ),
    )

    rendered = "\n".join(result.messages)
    assert "service: running pid=42 health=ok" in rendered
    assert str(journal) not in rendered
    assert "secret" not in rendered


class _FakeRunner:
    def __init__(self, *, print_result: str = "") -> None:
        self.commands: list[tuple[str, ...]] = []
        self.print_result = print_result
        self.loaded = False

    def run(self, args: list[str], *, check: bool = False):
        self.commands.append(tuple(args))
        if args[:2] == ["launchctl", "bootstrap"]:
            self.loaded = True
            return _Result(0, "", "")
        if args[:2] == ["launchctl", "bootout"]:
            self.loaded = False
            return _Result(0, "", "")
        if args[:2] == ["launchctl", "kickstart"]:
            self.loaded = True
            return _Result(0, "", "")
        if args[:2] == ["launchctl", "print"] and self.loaded and self.print_result:
            return _Result(0, self.print_result, "")
        if args[:2] == ["launchctl", "print"] and self.loaded:
            return _Result(0, '{\n\t"PID" = 999;\n};\n', "")
        if args[:2] == ["launchctl", "print"]:
            return _Result(1, "", "not found")
        return _Result(0, "", "")


class _Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
