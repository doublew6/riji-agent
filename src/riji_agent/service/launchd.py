"""macOS launchd backend for riji-agent service management."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from riji_agent.paths import default_log_dir
from riji_agent.service.base import (
    DEFAULT_SERVICE_LABEL,
    CommandRunner,
    ServiceError,
    ServiceStatus,
    UnsupportedServiceTargetError,
    check_url,
    parse_pid,
    resolve_default_command,
)


@dataclass(frozen=True)
class LaunchdServiceConfig:
    executable: Path
    working_directory: Path
    arguments: tuple[str, ...] = ("serve",)
    log_dir: Path = field(default_factory=default_log_dir)
    plist_path: Path = Path.home() / "Library" / "LaunchAgents" / "ai.riji-agent.plist"
    label: str = DEFAULT_SERVICE_LABEL
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


def render_launchd_plist(config: LaunchdServiceConfig) -> bytes:
    payload = {
        "Label": config.label,
        "ProgramArguments": [str(config.executable), *config.arguments],
        "WorkingDirectory": str(config.working_directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(config.stdout_path),
        "StandardErrorPath": str(config.stderr_path),
    }
    return plistlib.dumps(payload, sort_keys=True)


def default_launchd_config(*, port: int = 8765) -> LaunchdServiceConfig:
    executable, arguments = resolve_default_command()
    return LaunchdServiceConfig(
        executable=executable,
        arguments=arguments,
        working_directory=Path.cwd().resolve(),
        port=port,
    )


class LaunchdServiceManager:
    def __init__(
        self,
        *,
        config: Optional[LaunchdServiceConfig] = None,
        runner: Optional[CommandRunner] = None,
        platform: Optional[str] = None,
        url_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.config = config or default_launchd_config()
        self.runner = runner or CommandRunner()
        self.platform = platform or sys.platform
        self._url_checker = url_checker or check_url
        self.domain = f"gui/{os.getuid()}"

    def install(self) -> ServiceStatus:
        self._require_darwin()
        self.config.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.config.plist_path.write_bytes(render_launchd_plist(self.config))
        self._bootout()
        self._run(["launchctl", "bootstrap", self.domain, str(self.config.plist_path)])
        return self.status()

    def uninstall(self) -> ServiceStatus:
        self._require_darwin()
        self._bootout()
        if self.config.plist_path.exists():
            self.config.plist_path.unlink()
        return self.status()

    def start(self) -> ServiceStatus:
        self._require_darwin()
        self._ensure_installed()
        if not self.status().loaded:
            self._run(["launchctl", "bootstrap", self.domain, str(self.config.plist_path)])
        self._run(["launchctl", "kickstart", "-k", f"{self.domain}/{self.config.label}"])
        return self.status()

    def stop(self) -> ServiceStatus:
        self._require_darwin()
        self._bootout()
        return self.status()

    def restart(self) -> ServiceStatus:
        self._require_darwin()
        self._ensure_installed()
        self._bootout()
        self._run(["launchctl", "bootstrap", self.domain, str(self.config.plist_path)])
        self._run(["launchctl", "kickstart", "-k", f"{self.domain}/{self.config.label}"])
        return self.status()

    def status(self) -> ServiceStatus:
        self._require_darwin()
        installed = self.config.plist_path.exists()
        result = self.runner.run(["launchctl", "print", f"{self.domain}/{self.config.label}"])
        loaded = result.returncode == 0
        pid = parse_pid(result.stdout) if loaded else None
        running = pid is not None
        health = "ok" if running and self._url_checker(self.config.health_url) else "unavailable"
        return ServiceStatus(
            installed=installed,
            loaded=loaded,
            running=running,
            pid=pid,
            label=self.config.label,
            definition_path=self.config.plist_path,
            health=health,
        )

    def logs(self, *, lines: int = 80) -> str:
        self._require_darwin()
        if not self.config.stdout_path.exists():
            return ""
        content = self.config.stdout_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-lines:])

    def _bootout(self) -> None:
        self.runner.run(["launchctl", "bootout", self.domain, str(self.config.plist_path)])

    def _ensure_installed(self) -> None:
        if not self.config.plist_path.exists():
            raise ServiceError("service is not installed")

    def _require_darwin(self) -> None:
        if self.platform != "darwin":
            raise UnsupportedServiceTargetError("launchd services are only supported on macOS")

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = self.runner.run(args)
        if result.returncode != 0:
            raise ServiceError("service command failed")
        return result
