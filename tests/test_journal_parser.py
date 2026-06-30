from datetime import date
from pathlib import Path

import pytest

from riji_agent.journal.models import NoteKind
from riji_agent.journal.parser import (
    JournalParseError,
    iter_note_files,
    parse_note,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_extracts_metadata_and_stable_source_id(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(
        root / "daily" / "2026-06-24.md",
        "---\n"
        "date: 2026-06-24\n"
        "tags: [work, ai]\n"
        "---\n\n"
        "# 2026-06-24\n\n"
        "## 🌆 Evening\n今天梳理了 agent 架构设计。\n",
    )

    parsed = parse_note(note, root)

    assert parsed.source_id == "riji/daily/2026-06-24"
    assert parsed.relative_path == "daily/2026-06-24.md"
    assert parsed.kind is NoteKind.DAILY
    assert parsed.note_date == date(2026, 6, 24)
    assert parsed.title == "2026-06-24"
    assert parsed.tags == ("work", "ai")
    assert parsed.private is False
    assert "架构设计" in parsed.body
    assert "---" not in parsed.body  # frontmatter is stripped
    assert len(parsed.content_hash) == 64


def test_private_frontmatter_flag_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "daily" / "secret.md", "---\nprivate: true\n---\n# x\n机密\n")
    assert parse_note(note, root).private is True


def test_date_falls_back_to_filename(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "daily" / "2026-01-02.md", "# no frontmatter date\n内容\n")
    assert parse_note(note, root).note_date == date(2026, 1, 2)


def test_title_prefers_frontmatter_over_heading(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "weekly" / "w.md", "---\ntitle: 本周复盘\n---\n# 其它标题\n")
    parsed = parse_note(note, root)
    assert parsed.title == "本周复盘"
    assert parsed.kind is NoteKind.WEEKLY


def test_tags_accept_a_plain_string(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "monthly" / "m.md", "---\ntags: '#trip travel trip'\n---\n#月度\n")
    assert parse_note(note, root).tags == ("trip", "travel")


def test_invalid_frontmatter_raises(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "daily" / "bad.md", "---\ntags: [unclosed\n---\nbody\n")
    with pytest.raises(JournalParseError):
        parse_note(note, root)


def test_non_mapping_frontmatter_raises(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "daily" / "list.md", "---\n- just\n- a list\n---\nbody\n")
    with pytest.raises(JournalParseError):
        parse_note(note, root)


def test_file_outside_journal_folders_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    note = _write(root / "inbox" / "loose.md", "# loose\n")
    with pytest.raises(JournalParseError):
        parse_note(note, root)


def test_iter_note_files_only_yields_period_folders(tmp_path: Path) -> None:
    root = tmp_path / "riji"
    _write(root / "daily" / "2026-06-24.md", "# d\n")
    _write(root / "weekly" / "2026-W26.md", "# w\n")
    _write(root / "templates" / "daily.md", "# template\n")
    _write(root / "README.md", "# readme\n")

    found = {p.relative_to(root).as_posix() for p in iter_note_files(root)}
    assert found == {"daily/2026-06-24.md", "weekly/2026-W26.md"}


def test_crlf_frontmatter_is_parsed_and_private_detected(tmp_path: Path) -> None:
    # Windows/Obsidian vaults often save CRLF; frontmatter (including
    # `private: true`) must still be parsed so private notes are never leaked.
    root = tmp_path / "riji"
    path = root / "daily" / "2026-06-25.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    crlf = (
        "---\r\n"
        "date: 2026-06-25\r\n"
        "title: Secret Day\r\n"
        "tags: [private, work]\r\n"
        "private: true\r\n"
        "---\r\n"
        "# 2026-06-25\r\n"
        "绝密内容\r\n"
    )
    # Write explicit CRLF bytes so this reproduces on any OS, not just Windows.
    path.write_bytes(crlf.encode("utf-8"))

    note = parse_note(path, root)

    assert note.private is True
    assert note.note_date == date(2026, 6, 25)
    assert note.title == "Secret Day"
    assert note.tags == ("private", "work")
    # Body must not retain carriage returns after normalization.
    assert "\r" not in note.body
