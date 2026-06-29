from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.service import (
    LaunchdServiceManager,
    UnsupportedServiceTargetError,
    WindowsServiceManager,
    build_service_manager,
    default_target,
    supported_service_targets,
)


def test_registry_exposes_launchd_and_windows_backends() -> None:
    names = supported_service_targets()
    assert "launchd" in names
    assert "windows" in names


def test_build_launchd_manager_by_name() -> None:
    manager = build_service_manager("launchd")
    assert isinstance(manager, LaunchdServiceManager)


def test_build_windows_manager_by_name() -> None:
    manager = build_service_manager("windows")
    assert isinstance(manager, WindowsServiceManager)


def test_unknown_target_is_rejected_safely() -> None:
    with pytest.raises(UnsupportedServiceTargetError) as exc:
        build_service_manager("nope")
    # The error must not leak the requested target or any path/secret detail.
    assert "nope" not in str(exc.value)


def test_default_target_resolves_launchd_on_macos() -> None:
    assert default_target("darwin") == "launchd"


def test_default_target_resolves_windows_on_win32() -> None:
    assert default_target("win32") == "windows"


def test_default_target_has_no_backend_for_linux_yet() -> None:
    with pytest.raises(UnsupportedServiceTargetError):
        default_target("linux")


def test_service_package_has_no_core_or_transport_imports() -> None:
    service_files = [
        path
        for path in (Path(__file__).resolve().parents[1] / "src" / "riji_agent" / "service").glob("*.py")
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in service_files)

    forbidden = (
        "journal_root",
        "RIJI_JOURNAL_ROOT",
        "FeishuMessage",
        "HermesGateway",
        "import sqlite3",
    )
    for phrase in forbidden:
        assert phrase not in text, f"service package should not reference {phrase!r}"
