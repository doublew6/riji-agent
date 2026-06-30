from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PRD = ROOT / "docs" / "PRD.md"
ARCHITECTURE = ROOT / "docs" / "architecture" / "mvp-architecture.md"
PRIVACY = ROOT / "docs" / "privacy.md"
SECURITY = ROOT / "SECURITY.md"
LICENSE = ROOT / "LICENSE"
DEPLOYMENT = ROOT / "docs" / "deployment.md"
PACKS = ROOT / "docs" / "architecture" / "packs.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_positions_default_stack_without_lock_in() -> None:
    text = _read(README)

    assert "local-first" in text
    assert "Feishu + Hermes + DeepSeek" in text
    assert "default stack" in text
    assert "not the only supported architecture" in text


def test_privacy_boundary_is_explicit_in_public_docs() -> None:
    text = "\n".join(_read(path) for path in (README, PRD, ARCHITECTURE))

    assert "not a zero-egress system" in text
    assert "complete vault" in text
    assert "SQLite" in text
    assert "API keys" in text
    assert "bounded journal snippets" in text


def test_public_docs_do_not_contain_personal_paths() -> None:
    text = "\n".join(_read(path) for path in (README, PRD, ARCHITECTURE))

    forbidden = (
        "/Users/example",
        "iCloud" + "~md~obsidian",
        "/Users/example/Documents/ai_agent/riji-agent",
    )
    for phrase in forbidden:
        assert phrase not in text


def test_architecture_defines_replaceable_module_boundaries() -> None:
    text = _read(ARCHITECTURE)

    required_phrases = (
        "core/",
        "im/",
        "agent/",
        "models/",
        "Core must not depend on Feishu, Hermes, or DeepSeek",
        "IM adapters only map external chat payloads",
        "Agent runtimes only orchestrate registered tools",
        "Model providers only adapt model APIs",
        "current code to target modules",
    )
    for phrase in required_phrases:
        assert phrase in text


def test_release_privacy_and_security_docs_exist() -> None:
    privacy = _read(PRIVACY)
    security = _read(SECURITY)
    license_text = _read(LICENSE)

    for phrase in (
        "not a zero-egress system",
        "Feishu/Lark",
        "DeepSeek/default model provider",
        "complete vault",
        "SQLite",
        "API keys",
        "private: true",
    ):
        assert phrase in privacy

    for phrase in (
        "Do not include secrets",
        "Security reports",
        "git log --all --name-only",
        "PRIVATE_DEMO_SENTINEL",
    ):
        assert phrase in security

    assert "MIT License" in license_text


def test_docs_explain_cross_platform_service_and_sleep_behavior() -> None:
    text = _read(README) + "\n" + _read(DEPLOYMENT)

    for phrase in (
        "riji-agent service install",
        "riji-agent service start",
        "riji-agent service status",
        # All three backends and the auto default are documented.
        "launchd",
        "systemd",
        "windows",
        "auto",
        "ai.riji-agent",
        "127.0.0.1",
        # Sleep / logout behavior must be explained for the cross-platform case.
        "睡眠",
        "Hermes gateway",
    ):
        assert phrase in text


def test_docs_explain_personal_growth_pack_boundary() -> None:
    text = _read(README) + "\n" + _read(PACKS)

    for phrase in (
        "personal-growth",
        "whit-riji-skills",
        "codex-automations",
        "capability metadata",
        "controlled writer",
        "draft preview",
        "complete vault",
    ):
        assert phrase in text
