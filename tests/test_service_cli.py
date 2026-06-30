from __future__ import annotations

from pathlib import Path

import pytest

import riji_agent.main as main_module
from riji_agent.service import ServiceStatus


class _StubManager:
    def status(self) -> ServiceStatus:
        return ServiceStatus(
            installed=False,
            loaded=False,
            running=False,
            pid=None,
            label="stub",
            definition_path=Path("/tmp/stub"),
            health="unknown",
        )


def _record_build(captured: dict[str, str]):
    def build(target: str) -> _StubManager:
        captured["target"] = target
        return _StubManager()

    return build


def test_service_auto_resolves_to_platform_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(main_module, "default_target", lambda: "systemd")
    monkeypatch.setattr(main_module, "build_service_manager", _record_build(captured))

    with pytest.raises(SystemExit) as exc:
        main_module.main(["service", "status"])

    assert exc.value.code == 0
    assert captured["target"] == "systemd"


def test_service_explicit_target_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(main_module, "build_service_manager", _record_build(captured))

    with pytest.raises(SystemExit) as exc:
        main_module.main(["service", "status", "--target", "windows"])

    assert exc.value.code == 0
    assert captured["target"] == "windows"
