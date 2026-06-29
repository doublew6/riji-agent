from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.service import (
    SystemdServiceConfig,
    SystemdServiceManager,
    UnsupportedServiceTargetError,
    render_systemd_unit,
)


def _config(tmp_path: Path) -> SystemdServiceConfig:
    return SystemdServiceConfig(
        executable=tmp_path / "bin" / "riji-agent",
        working_directory=tmp_path / "repo",
        log_dir=tmp_path / "logs",
        unit_dir=tmp_path / "systemd",
    )


def test_unit_contains_service_shape_without_secrets(tmp_path: Path) -> None:
    config = _config(tmp_path)
    unit = render_systemd_unit(config)

    assert "Description=riji-agent local journal service" in unit
    assert f"ExecStart={config.executable} serve" in unit
    assert f"WorkingDirectory={config.working_directory}" in unit
    assert "Restart=on-failure" in unit
    assert f"StandardOutput=append:{config.stdout_path}" in unit
    assert "WantedBy=default.target" in unit
    assert "DEEPSEEK_API_KEY" not in unit
    assert "HERMES_SHARED_SECRET" not in unit
    assert "RIJI_JOURNAL_ROOT" not in unit


def test_install_writes_unit_and_enables(tmp_path: Path) -> None:
    runner = _FakeRunner()
    manager = SystemdServiceManager(
        config=_config(tmp_path),
        runner=runner,
        platform="linux",
        url_checker=lambda _url: True,
    )

    first = manager.install()
    second = manager.install()

    assert first.installed is True
    assert second.installed is True
    assert manager.config.unit_path.exists()
    enables = [c for c in runner.commands if c[:3] == ("systemctl", "--user", "enable")]
    reloads = [c for c in runner.commands if c[:3] == ("systemctl", "--user", "daemon-reload")]
    assert len(enables) == 2
    assert len(reloads) == 2


def test_start_stop_restart_status_and_logs_use_injected_runner(tmp_path: Path) -> None:
    runner = _FakeRunner()
    manager = SystemdServiceManager(
        config=_config(tmp_path),
        runner=runner,
        platform="linux",
        url_checker=lambda _url: True,
    )
    manager.install()
    manager.config.stdout_path.write_text("started\nready\n", encoding="utf-8")

    assert manager.start().running is True
    assert manager.stop().running is False
    assert manager.restart().running is True
    status = manager.status()
    logs = manager.logs(lines=1)

    assert status.running is True
    assert status.pid == 4321
    assert status.health == "ok"
    assert status.label == "riji-agent.service"
    assert status.definition_path == manager.config.unit_path
    assert logs == "ready"


def test_uninstall_removes_only_the_unit(tmp_path: Path) -> None:
    data_file = tmp_path / "data.sqlite3"
    log_file = tmp_path / "logs" / "service.log"
    data_file.write_text("keep", encoding="utf-8")
    manager = SystemdServiceManager(
        config=_config(tmp_path),
        runner=_FakeRunner(),
        platform="linux",
        url_checker=lambda _url: False,
    )
    manager.install()
    log_file.write_text("keep", encoding="utf-8")

    status = manager.uninstall()

    assert status.installed is False
    assert not manager.config.unit_path.exists()
    assert data_file.exists()
    assert log_file.exists()


def test_refuses_unsupported_platform(tmp_path: Path) -> None:
    manager = SystemdServiceManager(
        config=_config(tmp_path),
        runner=_FakeRunner(),
        platform="darwin",
        url_checker=lambda _url: False,
    )

    with pytest.raises(UnsupportedServiceTargetError):
        manager.install()


class _FakeRunner:
    """Stateful fake systemctl: start/stop toggle ActiveState; show reports it."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.active = False

    def run(self, args: list[str], *, check: bool = False):
        self.commands.append(tuple(args))
        verb = args[2] if len(args) > 2 else ""
        if verb in {"start", "restart"}:
            self.active = True
            return _Result(0, "", "")
        if verb in {"stop", "disable"}:
            self.active = False
            return _Result(0, "", "")
        if verb in {"daemon-reload", "enable"}:
            return _Result(0, "", "")
        if verb == "show":
            active = "active" if self.active else "inactive"
            pid = "4321" if self.active else "0"
            return _Result(0, f"LoadState=loaded\nActiveState={active}\nMainPID={pid}\n", "")
        return _Result(0, "", "")


class _Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
