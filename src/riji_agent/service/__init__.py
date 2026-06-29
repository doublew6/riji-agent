"""Local background service management for riji-agent.

The package is organized as a backend-neutral contract (:mod:`base`) plus one
module per platform backend and a :mod:`registry` that resolves a target name to
a manager. macOS launchd is the first backend; systemd and Windows Task
Scheduler implement the same :class:`ServiceManager` shape later. Public names
are re-exported here so callers import from ``riji_agent.service`` directly.
"""

from __future__ import annotations

from riji_agent.service.base import (
    DEFAULT_SERVICE_LABEL,
    CommandRunner,
    ServiceError,
    ServiceManager,
    ServiceStatus,
    UnsupportedServiceTargetError,
)
from riji_agent.service.launchd import (
    LaunchdServiceConfig,
    LaunchdServiceManager,
    default_launchd_config,
    render_launchd_plist,
)
from riji_agent.service.registry import (
    build_service_manager,
    default_target,
    get_default_service_status,
    register_service_backend,
    supported_service_targets,
)

__all__ = [
    "DEFAULT_SERVICE_LABEL",
    "CommandRunner",
    "ServiceError",
    "ServiceManager",
    "ServiceStatus",
    "UnsupportedServiceTargetError",
    "LaunchdServiceConfig",
    "LaunchdServiceManager",
    "default_launchd_config",
    "render_launchd_plist",
    "build_service_manager",
    "default_target",
    "get_default_service_status",
    "register_service_backend",
    "supported_service_targets",
]
