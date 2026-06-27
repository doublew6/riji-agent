from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PRD = ROOT / "docs" / "PRD.md"
ARCHITECTURE = ROOT / "docs" / "architecture" / "mvp-architecture.md"


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
        "icloud-backed-vault",
        "/Users/example/Documents/riji-agent",
    )
    for phrase in forbidden:
        assert phrase not in text
