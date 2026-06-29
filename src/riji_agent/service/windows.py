"""Windows Task Scheduler backend for riji-agent service management.

Uses ``schtasks`` with a logon-triggered task that runs as the current user, so
it needs **no administrator / UAC** and matches launchd's "start after the user
is present" semantics. The command surface mirrors :class:`LaunchdServiceManager`
(same injectable ``runner`` / ``platform`` / ``url_checker``) so it is testable
on any OS with a fake :class:`CommandRunner`.

Task Scheduler does not redirect a task's stdout/stderr the way launchd's plist
does, so the action runs through ``cmd.exe /c`` with ``>>`` redirection into the
same ``service.log`` / ``service.error.log`` files, keeping ``logs()`` identical
across backends.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape

from riji_agent.paths import default_log_dir
from riji_agent.service.base import (
    CommandRunner,
    ServiceError,
    ServiceStatus,
    UnsupportedServiceTargetError,
    check_url,
    parse_pid,
    resolve_default_command,
)


DEFAULT_TASK_NAME = "ai.riji-agent"
_TASK_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


@dataclass(frozen=True)
class WindowsServiceConfig:
    executable: Path
    working_directory: Path
    arguments: tuple[str, ...] = ("serve",)
    log_dir: Path = field(default_factory=default_log_dir)
    task_name: str = DEFAULT_TASK_NAME
    port: int = 8765

    @property
    def stdout_path(self) -> Path:
        return self.log_dir / "service.log"

    @property
    def stderr_path(self) -> Path:
        return self.log_dir / "service.error.log"

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/healthz"

    @property
    def task_path(self) -> Path:
        # The task's identity in the scheduler tree, used as the definition path
        # in ServiceStatus (the launchd equivalent of the plist path).
        return Path("\\" + self.task_name)


def _service_command_line(config: WindowsServiceConfig) -> str:
    parts = [f'"{config.executable}"']
    args = " ".join(config.arguments)
    if args:
        parts.append(args)
    parts.append(f'>> "{config.stdout_path}"')
    parts.append(f'2>> "{config.stderr_path}"')
    return " ".join(parts)


def render_task_xml(config: WindowsServiceConfig) -> str:
    arguments = f'/c "{_service_command_line(config)}"'
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_TASK_XML_NS}">\n'
        "  <RegistrationInfo>\n"
        "    <Description>riji-agent local journal service</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>999</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>cmd.exe</Command>\n"
        f"      <Arguments>{escape(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{escape(str(config.working_directory))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def default_windows_config(*, port: int = 8765) -> WindowsServiceConfig:
    executable, arguments = resolve_default_command()
    return WindowsServiceConfig(
        executable=executable,
        arguments=arguments,
        working_directory=Path.cwd().resolve(),
        port=port,
    )


class WindowsServiceManager:
    def __init__(
        self,
        *,
        config: Optional[WindowsServiceConfig] = None,
        runner: Optional[CommandRunner] = None,
        platform: Optional[str] = None,
        url_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.config = config or default_windows_config()
        self.runner = runner or CommandRunner()
        self.platform = platform or sys.platform
        self._url_checker = url_checker or check_url

    def install(self) -> ServiceStatus:
        self._require_windows()
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        # schtasks expects the import file to exist on disk; write a transient
        # UTF-16 XML, register the task (/f makes re-install idempotent), then
        # remove the temp file. No secrets are ever written into it.
        fd, tmp_name = tempfile.mkstemp(suffix=".xml", dir=str(self.config.log_dir))
        tmp_path = Path(tmp_name)
        try:
            os.close(fd)
            tmp_path.write_text(render_task_xml(self.config), encoding="utf-16")
            self._run(
                ["schtasks", "/create", "/tn", self.config.task_name, "/xml", str(tmp_path), "/f"]
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        return self.status()

    def uninstall(self) -> ServiceStatus:
        self._require_windows()
        self.runner.run(["schtasks", "/end", "/tn", self.config.task_name])
        self.runner.run(["schtasks", "/delete", "/tn", self.config.task_name, "/f"])
        return self.status()

    def start(self) -> ServiceStatus:
        self._require_windows()
        self._ensure_installed()
        self._run(["schtasks", "/run", "/tn", self.config.task_name])
        return self.status()

    def stop(self) -> ServiceStatus:
        self._require_windows()
        self._run(["schtasks", "/end", "/tn", self.config.task_name])
        return self.status()

    def restart(self) -> ServiceStatus:
        self._require_windows()
        self._ensure_installed()
        self.runner.run(["schtasks", "/end", "/tn", self.config.task_name])
        self._run(["schtasks", "/run", "/tn", self.config.task_name])
        return self.status()

    def status(self) -> ServiceStatus:
        self._require_windows()
        result = self.runner.run(
            ["schtasks", "/query", "/tn", self.config.task_name, "/fo", "LIST", "/v"]
        )
        installed = result.returncode == 0
        running = installed and _is_running(result.stdout)
        pid = parse_pid(result.stdout) if installed else None
        health = "ok" if running and self._url_checker(self.config.health_url) else "unavailable"
        return ServiceStatus(
            installed=installed,
            loaded=installed,
            running=running,
            pid=pid,
            label=self.config.task_name,
            plist_path=self.config.task_path,
            health=health,
        )

    def logs(self, *, lines: int = 80) -> str:
        self._require_windows()
        if not self.config.stdout_path.exists():
            return ""
        content = self.config.stdout_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-lines:])

    def _ensure_installed(self) -> None:
        result = self.runner.run(["schtasks", "/query", "/tn", self.config.task_name])
        if result.returncode != 0:
            raise ServiceError("service is not installed")

    def _require_windows(self) -> None:
        if self.platform != "win32":
            raise UnsupportedServiceTargetError(
                "Task Scheduler services are only supported on Windows"
            )

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = self.runner.run(args)
        if result.returncode != 0:
            raise ServiceError("service command failed")
        return result


def _is_running(output: str) -> bool:
    # Verbose `schtasks /query /fo LIST /v` reports a "Status:" line; an actively
    # executing task instance shows "Running" (English Windows assumed).
    return re.search(r"^\s*Status:\s*Running\s*$", output, re.MULTILINE | re.IGNORECASE) is not None
