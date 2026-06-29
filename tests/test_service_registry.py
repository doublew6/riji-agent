from __future__ import annotations

from pathlib import Path

import pytest

from riji_agent.service import (
    LaunchdServiceManager,
    UnsupportedServiceTargetError,
    build_service_manager,
    default_target,
    supported_service_targets,
)


def test_registry_exposes_launchd_backend() -> None:
    assert "launchd" in supported_service_targets()


def test_build_launchd_manager_by_name() -> None:
    manager = build_service_manager("launchd")
    assert isinstance(manager, LaunchdServiceManager)


def test_unknown_target_is_rejected_safely() -> None:
    with pytest.raises(UnsupportedServiceTargetError) as exc:
        build_service_manager("nope")
    # The error must not leak the requested target or any path/secret detail.
    assert "nope" not in str(exc.value)


def test_default_target_resolves_launchd_on_macos() -> None:
    assert default_target("darwin") == "launchd"


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_default_target_has_no_backend_for_other_platforms_yet(platform: str) -> None:
    with pytest.raises(UnsupportedServiceTargetError):
        default_target(platform)


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
