"""Service backend registry: map a target name to a ServiceManager factory.

Mirrors the model / IM / agent layers (see docs/architecture/modules.md): a
neutral contract, a default adapter, and a registry that resolves a name. Adding
a backend means *registering* it here — the CLI never grows a platform branch.
"""

from __future__ import annotations

import sys
from typing import Callable, Dict

from riji_agent.service.base import (
    ServiceManager,
    ServiceStatus,
    UnsupportedServiceTargetError,
)
from riji_agent.service.launchd import LaunchdServiceManager
from riji_agent.service.systemd import SystemdServiceManager
from riji_agent.service.windows import WindowsServiceManager


ServiceManagerFactory = Callable[[], ServiceManager]

_BACKENDS: Dict[str, ServiceManagerFactory] = {}

# Maps the host platform (sys.platform) to its native service backend name.
_PLATFORM_DEFAULT: Dict[str, str] = {
    "darwin": "launchd",
    "linux": "systemd",
    "win32": "windows",
}


def register_service_backend(name: str, factory: ServiceManagerFactory) -> None:
    _BACKENDS[name] = factory


def supported_service_targets() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


def build_service_manager(target: str) -> ServiceManager:
    factory = _BACKENDS.get(target)
    if factory is None:
        raise UnsupportedServiceTargetError("unsupported service target")
    return factory()


def default_target(platform: str | None = None) -> str:
    """Return the native backend name for ``platform`` (default: this host)."""
    name = _PLATFORM_DEFAULT.get(platform or sys.platform)
    if name is None:
        raise UnsupportedServiceTargetError("no service backend for this platform")
    return name


def get_default_service_status() -> ServiceStatus:
    return build_service_manager(default_target()).status()


register_service_backend("launchd", lambda: LaunchdServiceManager())
register_service_backend("systemd", lambda: SystemdServiceManager())
register_service_backend("windows", lambda: WindowsServiceManager())
