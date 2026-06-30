"""Backend-neutral service management contract and shared helpers.

Every supported backend (macOS launchd, and later Linux systemd / Windows Task
Scheduler) implements the same :class:`ServiceManager` shape so the CLI never has
to know which platform it is on. Anything OS-independent — the status type, error
types, the subprocess runner, and the small parsing/health helpers — lives here
and is reused by each backend.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


DEFAULT_SERVICE_LABEL = "ai.riji-agent"


class ServiceError(RuntimeError):
    """Safe service-management error; never includes secrets or env contents."""


class UnsupportedServiceTargetError(ServiceError):
    """Raised when the selected service backend is not available here."""


@dataclass(frozen=True)
class ServiceStatus:
    installed: bool
    loaded: bool
    running: bool
    pid: Optional[int]
    label: str
    # Backend-neutral path to the service definition: a launchd plist, a systemd
    # unit file, or a Windows scheduled-task path.
    definition_path: Path
    health: str = "unknown"


class CommandRunner:
    def run(self, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            check=check,
            text=True,
            capture_output=True,
        )


@runtime_checkable
class ServiceManager(Protocol):
    """The command surface every service backend exposes.

    The CLI resolves a backend by name and drives it through these methods only;
    backend-specific details (plist, unit file, scheduled task) stay private.
    """

    def install(self) -> ServiceStatus: ...

    def uninstall(self) -> ServiceStatus: ...

    def start(self) -> ServiceStatus: ...

    def stop(self) -> ServiceStatus: ...

    def restart(self) -> ServiceStatus: ...

    def status(self) -> ServiceStatus: ...

    def logs(self, *, lines: int = 80) -> str: ...


def resolve_executable() -> Path:
    candidate = Path(sys.argv[0]).expanduser()
    if candidate.exists():
        return candidate.resolve()
    found = shutil.which("riji-agent")
    if found:
        return Path(found).resolve()
    raise ServiceError("could not locate riji-agent executable")


def resolve_default_command() -> tuple[Path, tuple[str, ...]]:
    uv = shutil.which("uv")
    if uv:
        return Path(uv).resolve(), ("run", "riji-agent", "serve")
    return resolve_executable(), ("serve",)


def parse_pid(output: str) -> Optional[int]:
    match = re.search(r'(?:"PID"|pid)\s*=\s*(\d+)', output)
    if match:
        return int(match.group(1))
    return None


def check_url(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False
