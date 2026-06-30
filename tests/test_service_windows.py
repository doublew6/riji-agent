from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.service import (
    UnsupportedServiceTargetError,
    WindowsServiceConfig,
    WindowsServiceManager,
    render_task_xml,
)


def _config(tmp_path: Path) -> WindowsServiceConfig:
    return WindowsServiceConfig(
        executable=tmp_path / "bin" / "riji-agent.exe",
        working_directory=tmp_path / "repo",
        log_dir=tmp_path / "logs",
    )


def test_task_xml_contains_service_shape_without_secrets(tmp_path: Path) -> None:
    config = _config(tmp_path)
    xml = render_task_xml(config)

    assert "<LogonTrigger>" in xml
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml
    assert "<RestartOnFailure>" in xml
    assert "<Command>cmd.exe</Command>" in xml
    assert str(config.executable) in xml
    assert str(config.working_directory) in xml
    assert str(config.stdout_path) in xml
    assert str(config.stderr_path) in xml
    # No credentials or journal config are ever embedded in the task definition.
    assert "DEEPSEEK_API_KEY" not in xml
    assert "HERMES_SHARED_SECRET" not in xml
    assert "RIJI_JOURNAL_ROOT" not in xml


def test_install_is_idempotent_and_forces_recreate(tmp_path: Path) -> None:
    runner = _FakeRunner()
    manager = WindowsServiceManager(
        config=_config(tmp_path),
        runner=runner,
        platform="win32",
        url_checker=lambda _url: True,
    )

    first = manager.install()
    second = manager.install()

    assert first.installed is True
    assert second.installed is True
    creates = [c for c in runner.commands if c[:2] == ("schtasks", "/create")]
    assert len(creates) == 2
    # /f makes re-install replace the task rather than fail or duplicate.
    assert all("/f" in c for c in creates)
    # No XML artifact is left behind in the log dir.
    assert list((tmp_path / "logs").glob("*.xml")) == []


def test_start_stop_restart_status_and_logs_use_injected_runner(tmp_path: Path) -> None:
    runner = _FakeRunner()
    manager = WindowsServiceManager(
        config=_config(tmp_path),
        runner=runner,
        platform="win32",
        url_checker=lambda _url: True,
    )
    manager.install()
    (manager.config.log_dir).mkdir(parents=True, exist_ok=True)
    (manager.config.stdout_path).write_text("started\nready\n", encoding="utf-8")

    assert manager.start().running is True
    assert manager.stop().running is False
    assert manager.restart().running is True
    status = manager.status()
    logs = manager.logs(lines=1)

    assert status.installed is True
    assert status.running is True
    assert status.health == "ok"
    assert status.label == "ai.riji-agent"
    assert logs == "ready"


def test_uninstall_removes_only_the_task(tmp_path: Path) -> None:
    data_file = tmp_path / "data.sqlite3"
    env_file = tmp_path / ".env"
    log_file = tmp_path / "logs" / "service.log"
    data_file.write_text("keep", encoding="utf-8")
    env_file.write_text("keep", encoding="utf-8")
    runner = _FakeRunner()
    manager = WindowsServiceManager(
        config=_config(tmp_path),
        runner=runner,
        platform="win32",
        url_checker=lambda _url: False,
    )
    manager.install()
    log_file.write_text("keep", encoding="utf-8")

    status = manager.uninstall()

    assert status.installed is False
    deletes = [c for c in runner.commands if c[:2] == ("schtasks", "/delete")]
    assert len(deletes) == 1
    assert data_file.exists()
    assert env_file.exists()
    assert log_file.exists()


def test_refuses_unsupported_platform(tmp_path: Path) -> None:
    manager = WindowsServiceManager(
        config=_config(tmp_path),
        runner=_FakeRunner(),
        platform="darwin",
        url_checker=lambda _url: False,
    )

    with pytest.raises(UnsupportedServiceTargetError):
        manager.install()


class _FakeRunner:
    """Stateful fake schtasks: create/delete toggle existence, run/end toggle run."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.installed = False
        self.running = False

    def run(self, args: list[str], *, check: bool = False):
        self.commands.append(tuple(args))
        head = tuple(args[:2])
        if head == ("schtasks", "/create"):
            self.installed = True
            return _Result(0, "", "")
        if head == ("schtasks", "/delete"):
            self.installed = False
            self.running = False
            return _Result(0, "", "")
        if head == ("schtasks", "/run"):
            self.running = True
            return _Result(0, "", "")
        if head == ("schtasks", "/end"):
            self.running = False
            return _Result(0, "", "")
        if head == ("schtasks", "/query"):
            if not self.installed:
                return _Result(1, "", "ERROR: The system cannot find the file specified.")
            state = "Running" if self.running else "Ready"
            return _Result(
                0,
                "TaskName:                             \\ai.riji-agent\n"
                f"Status:                               {state}\n"
                "Scheduled Task State:                 Enabled\n",
                "",
            )
        return _Result(0, "", "")


class _Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
