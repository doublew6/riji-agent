"""Linux systemd (user) backend for riji-agent service management.

Uses ``systemctl --user`` with a generated unit file, so the service runs as the
current user without root and starts on login (``WantedBy=default.target``).
The command surface and injectable ``runner`` / ``platform`` / ``url_checker``
mirror the launchd and Windows backends, keeping the contract uniform and the
tests OS-independent.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

from riji_agent.paths import default_log_dir
from riji_agent.service.base import (
    CommandRunner,
    ServiceError,
    ServiceStatus,
    UnsupportedServiceTargetError,
    check_url,
)


DEFAULT_UNIT_NAME = "riji-agent.service"


def _default_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


@dataclass(frozen=True)
class SystemdServiceConfig:
    executable: Path
    working_directory: Path
    arguments: tuple[str, ...] = ("serve",)
    log_dir: Path = field(default_factory=default_log_dir)
    unit_dir: Path = field(default_factory=_default_unit_dir)
    unit_name: str = DEFAULT_UNIT_NAME
    port: int = 8765

    @property
    def unit_path(self) -> Path:
        return self.unit_dir / self.unit_name

    @property
    def stdout_path(self) -> Path:
        return self.log_dir / "service.log"

    @property
    def stderr_path(self) -> Path:
        return self.log_dir / "service.error.log"

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/healthz"


def render_systemd_unit(config: SystemdServiceConfig) -> str:
    exec_start = " ".join([str(config.executable), *config.arguments])
    return (
        "[Unit]\n"
        "Description=riji-agent local journal service\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={config.working_directory}\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"StandardOutput=append:{config.stdout_path}\n"
        f"StandardError=append:{config.stderr_path}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def default_systemd_config(*, port: int = 8765) -> SystemdServiceConfig:
    from riji_agent.service.base import resolve_default_command

    executable, arguments = resolve_default_command()
    return SystemdServiceConfig(
        executable=executable,
        arguments=arguments,
        working_directory=Path.cwd().resolve(),
        port=port,
    )


class SystemdServiceManager:
    def __init__(
        self,
        *,
        config: Optional[SystemdServiceConfig] = None,
        runner: Optional[CommandRunner] = None,
        platform: Optional[str] = None,
        url_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.config = config or default_systemd_config()
        self.runner = runner or CommandRunner()
        self.platform = platform or sys.platform
        self._url_checker = url_checker or check_url

    def install(self) -> ServiceStatus:
        self._require_linux()
        self.config.unit_dir.mkdir(parents=True, exist_ok=True)
        self.config.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.config.unit_path.write_text(render_systemd_unit(self.config), encoding="utf-8")
        self._run(["systemctl", "--user", "daemon-reload"])
        self._run(["systemctl", "--user", "enable", self.config.unit_name])
        return self.status()

    def uninstall(self) -> ServiceStatus:
        self._require_linux()
        self.runner.run(["systemctl", "--user", "disable", "--now", self.config.unit_name])
        if self.config.unit_path.exists():
            self.config.unit_path.unlink()
        self.runner.run(["systemctl", "--user", "daemon-reload"])
        return self.status()

    def start(self) -> ServiceStatus:
        self._require_linux()
        self._ensure_installed()
        self._run(["systemctl", "--user", "start", self.config.unit_name])
        return self.status()

    def stop(self) -> ServiceStatus:
        self._require_linux()
        self._run(["systemctl", "--user", "stop", self.config.unit_name])
        return self.status()

    def restart(self) -> ServiceStatus:
        self._require_linux()
        self._ensure_installed()
        self._run(["systemctl", "--user", "restart", self.config.unit_name])
        return self.status()

    def status(self) -> ServiceStatus:
        self._require_linux()
        installed = self.config.unit_path.exists()
        result = self.runner.run(
            [
                "systemctl",
                "--user",
                "show",
                self.config.unit_name,
                "--property=LoadState,ActiveState,MainPID",
            ]
        )
        props = _parse_properties(result.stdout)
        loaded = props.get("LoadState") == "loaded"
        running = props.get("ActiveState") == "active"
        pid = _parse_main_pid(props.get("MainPID"))
        health = "ok" if running and self._url_checker(self.config.health_url) else "unavailable"
        return ServiceStatus(
            installed=installed,
            loaded=loaded,
            running=running,
            pid=pid,
            label=self.config.unit_name,
            definition_path=self.config.unit_path,
            health=health,
        )

    def logs(self, *, lines: int = 80) -> str:
        self._require_linux()
        if not self.config.stdout_path.exists():
            return ""
        content = self.config.stdout_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-lines:])

    def _ensure_installed(self) -> None:
        if not self.config.unit_path.exists():
            raise ServiceError("service is not installed")

    def _require_linux(self) -> None:
        if self.platform != "linux":
            raise UnsupportedServiceTargetError("systemd services are only supported on Linux")

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = self.runner.run(args)
        if result.returncode != 0:
            raise ServiceError("service command failed")
        return result


def _parse_properties(output: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key.strip()] = value.strip()
    return props


def _parse_main_pid(value: Optional[str]) -> Optional[int]:
    if value and value.isdigit() and int(value) > 0:
        return int(value)
    return None
