from pathlib import Path

import pytest

from riji_agent.integrations.hermes_installer import (
    BEGIN_MARKER,
    END_MARKER,
    HermesBridgeInstallError,
    install,
    status,
    uninstall,
)


ANCHOR = "        # Intercept messages that are responses to a pending /update prompt."


def _gateway(path: Path) -> Path:
    text = "\n".join(
        [
            "async def dispatch(self, event, source, is_internal=False):",
            "    if source.platform:",
            "        pass",
            "",
            ANCHOR,
            "        pass",
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    return path


def test_install_inserts_managed_bridge_before_anchor(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path / "run.py")

    result = install(gateway)

    text = gateway.read_text(encoding="utf-8")
    assert result.installed
    assert BEGIN_MARKER in text
    assert END_MARKER in text
    assert text.index(BEGIN_MARKER) < text.index(ANCHOR)
    assert "trust_env=False" in text
    assert "RIJI_AGENT_TIMEOUT_SECONDS" in text
    assert "[[audio_as_voice]]" in text
    assert "MEDIA:" in text
    assert (tmp_path / "run.py.riji-agent.bak").exists()


def test_install_is_idempotent_and_updates_existing_block(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path / "run.py")
    install(gateway)
    once = gateway.read_text(encoding="utf-8")

    install(gateway)
    twice = gateway.read_text(encoding="utf-8")

    assert once == twice
    assert twice.count(BEGIN_MARKER) == 1


def test_install_replaces_legacy_patch(tmp_path: Path) -> None:
    gateway = tmp_path / "run.py"
    gateway.write_text(
        "\n".join(
            [
                "async def dispatch(self, event, source, is_internal=False):",
                "        # riji-agent local privacy bridge.",
                "        return 'old'",
                "",
                ANCHOR,
                "        pass",
                "",
            ]
        ),
        encoding="utf-8",
    )

    install(gateway, backup=False)
    text = gateway.read_text(encoding="utf-8")

    assert "return 'old'" not in text
    assert BEGIN_MARKER in text


def test_status_and_uninstall(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path / "run.py")
    assert status(gateway).state == "not_installed"

    install(gateway, backup=False)
    assert status(gateway).state == "installed"

    uninstall(gateway, backup=False)
    assert status(gateway).state == "not_installed"
    assert BEGIN_MARKER not in gateway.read_text(encoding="utf-8")


def test_missing_anchor_is_safe_error(tmp_path: Path) -> None:
    gateway = tmp_path / "run.py"
    gateway.write_text("print('no expected anchor')\n", encoding="utf-8")

    with pytest.raises(HermesBridgeInstallError):
        install(gateway)
