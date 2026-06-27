from __future__ import annotations

from pathlib import Path

from riji_agent.demo import sample_vault_root, run_demo_chat


def test_sample_vault_is_fictional_and_has_private_note() -> None:
    root = sample_vault_root()
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.md"))

    assert (root / "daily").is_dir()
    assert (root / "weekly").is_dir()
    assert (root / "monthly").is_dir()
    assert "private: true" in text
    assert "/Users/example" not in text
    assert "icloud-backed-vault" not in text


def test_demo_chat_uses_sample_vault_without_private_text(tmp_path: Path) -> None:
    answer = run_demo_chat("launch planning", data_dir=tmp_path)

    assert "Sources:" in answer
    assert "[[riji/daily/2026-01-05]]" in answer
    assert "PRIVATE_DEMO_SENTINEL" not in answer
