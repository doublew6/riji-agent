from datetime import date
from errno import EAGAIN
from pathlib import Path

import pytest

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.writer import commit_operations

TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    return root


def test_creates_new_note_from_template(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    outcome = commit_operations(root, date(2026, 6, 25), [DraftOperation("🌆 Evening", "评审通过")])

    path = root / "daily" / "2026-06-25.md"
    assert outcome.new_file is True
    assert outcome.source_id == "riji/daily/2026-06-25"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# 2026-06-25")
    assert "- 评审通过" in text


def test_retries_transient_template_read_failure(tmp_path: Path, monkeypatch) -> None:
    root = _vault(tmp_path)
    original_read_text = Path.read_text
    calls = {"count": 0}

    def flaky_read_text(path, *args, **kwargs):
        if path.name == "daily.md" and calls["count"] == 0:
            calls["count"] += 1
            raise OSError(EAGAIN, "Resource " + "deadlock avoided")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    outcome = commit_operations(
        root,
        date(2026, 6, 25),
        [DraftOperation("🌆 Evening", "评审通过")],
        retry_delay_seconds=0,
    )

    assert outcome.source_id == "riji/daily/2026-06-25"
    assert calls["count"] == 1
    assert "- 评审通过" in (root / "daily" / "2026-06-25.md").read_text(encoding="utf-8")


def test_persistent_transient_read_failure_still_raises_without_partial_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _vault(tmp_path)
    original_read_text = Path.read_text

    def failing_read_text(path, *args, **kwargs):
        if path.name == "daily.md":
            raise OSError(EAGAIN, "Resource " + "deadlock avoided")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)
    with pytest.raises(OSError):
        commit_operations(
            root,
            date(2026, 6, 25),
            [DraftOperation("🌆 Evening", "评审通过")],
            retry_attempts=2,
            retry_delay_seconds=0,
        )

    assert not (root / "daily" / "2026-06-25.md").exists()


def test_appends_to_existing_note(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    daily = root / "daily" / "2026-06-25.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("# 2026-06-25\n\n## 🌆 Evening\n- 早先的事\n", encoding="utf-8")

    outcome = commit_operations(root, date(2026, 6, 25), [DraftOperation("🌆 Evening", "later")])
    assert outcome.new_file is False
    assert outcome.before_hash != ""
    text = daily.read_text(encoding="utf-8")
    assert "- 早先的事" in text and "- later" in text


def test_missing_section_does_not_write_a_partial_file(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with pytest.raises(DraftError) as err:
        commit_operations(root, date(2026, 6, 25), [DraftOperation("不存在", "x")])
    assert err.value.code is DraftErrorCode.SECTION_NOT_FOUND
    assert not (root / "daily" / "2026-06-25.md").exists()  # no half file


def test_missing_template_for_new_note_raises(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    root.mkdir()
    with pytest.raises(DraftError) as err:
        commit_operations(root, date(2026, 6, 25), [DraftOperation("🌆 Evening", "x")])
    assert err.value.code is DraftErrorCode.TEMPLATE_NOT_FOUND
