"""Local background service management for riji-agent.

The first supported backend is macOS launchd. The public command surface is
kept backend-neutral so a future systemd manager can implement the same shape.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


DEFAULT_SERVICE_LABEL = "ai.riji-agent"


class ServiceError(RuntimeError):
    """Safe service-management error; never includes secrets or env contents."""


class UnsupportedServiceTargetError(ServiceError):
    """Raised when the selected service backend is not available here."""


@dataclass(frozen=True)
class LaunchdServiceConfig:
    executable: Path
    working_directory: Path
    arguments: tuple[str, ...] = ("serve",)
    log_dir: Path = Path.home() / ".riji-agent" / "logs"
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


@dataclass(frozen=True)
class ServiceStatus:
    installed: bool
    loaded: bool
    running: bool
    pid: Optional[int]
    label: str
    plist_path: Path
    health: str = "unknown"


class CommandRunner:
    def run(self, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            check=check,
            text=True,
            capture_output=True,
        )


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
    executable, arguments = _resolve_default_command()
    return LaunchdServiceConfig(
        executable=executable,
        arguments=arguments,
        working_directory=Path.cwd().resolve(),
        port=port,
    )


def get_default_service_status() -> ServiceStatus:
    return LaunchdServiceManager().status()


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
        self._url_checker = url_checker or _check_url
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
        pid = _parse_pid(result.stdout) if loaded else None
        running = pid is not None
        health = "ok" if running and self._url_checker(self.config.health_url) else "unavailable"
        return ServiceStatus(
            installed=installed,
            loaded=loaded,
            running=running,
            pid=pid,
            label=self.config.label,
            plist_path=self.config.plist_path,
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


def _resolve_executable() -> Path:
    candidate = Path(sys.argv[0]).expanduser()
    if candidate.exists():
        return candidate.resolve()
    found = shutil.which("riji-agent")
    if found:
        return Path(found).resolve()
    raise ServiceError("could not locate riji-agent executable")


def _resolve_default_command() -> tuple[Path, tuple[str, ...]]:
    uv = shutil.which("uv")
    if uv:
        return Path(uv).resolve(), ("run", "riji-agent", "serve")
    return _resolve_executable(), ("serve",)


def _parse_pid(output: str) -> Optional[int]:
    match = re.search(r'(?:"PID"|pid)\s*=\s*(\d+)', output)
    if match:
        return int(match.group(1))
    return None


def _check_url(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False
