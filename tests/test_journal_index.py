import hashlib
from pathlib import Path

import pytest

from riji_agent.journal.index import JournalIndex
from riji_agent.journal.parser import build_source_id


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    _write(
        root / "daily" / "2026-06-24.md",
        "---\ndate: 2026-06-24\ntags: [ai]\n---\n# 2026-06-24\n今天梳理了 agent 架构设计与索引。\n",
    )
    _write(
        root / "daily" / "2026-06-25.md",
        "---\ndate: 2026-06-25\nprivate: true\n---\n# 2026-06-25\n机密的私人想法记录。\n",
    )
    _write(root / "weekly" / "2026-W26.md", "---\ntitle: 本周复盘\n---\n本周完成了骨架。\n")
    return root


@pytest.fixture
def index(tmp_path: Path, vault: Path) -> JournalIndex:
    idx = JournalIndex(database_path=tmp_path / "data" / "index.sqlite3", journal_root=vault)
    yield idx
    idx.close()


def test_full_build_indexes_every_note(index: JournalIndex) -> None:
    stats = index.build_index()
    assert (stats.added, stats.updated, stats.deleted) == (3, 0, 0)
    assert index.count() == 3


def test_rebuild_is_idempotent(index: JournalIndex) -> None:
    index.build_index()
    stats = index.build_index()
    assert (stats.added, stats.updated, stats.deleted) == (0, 0, 0)
    assert stats.unchanged == 3


def test_incremental_reindexes_only_changed_note(index: JournalIndex, vault: Path) -> None:
    index.build_index()
    (vault / "daily" / "2026-06-24.md").write_text(
        "---\ndate: 2026-06-24\n---\n# 2026-06-24\n更新后的内容。\n", encoding="utf-8"
    )

    stats = index.build_index()

    assert stats.updated == 1
    assert stats.unchanged == 2
    assert stats.added == 0


def test_detects_added_and_deleted_notes(index: JournalIndex, vault: Path) -> None:
    index.build_index()
    (vault / "daily" / "2026-06-24.md").unlink()
    _write(vault / "daily" / "2026-06-26.md", "# 2026-06-26\n新的一天。\n")

    stats = index.build_index()

    assert stats.added == 1
    assert stats.deleted == 1
    assert index.get("riji/daily/2026-06-24") is None


def test_update_single_note_without_full_walk(index: JournalIndex, vault: Path) -> None:
    index.build_index()
    path = vault / "weekly" / "2026-W26.md"
    path.write_text("---\ntitle: 改了标题\n---\n内容也改了。\n", encoding="utf-8")

    note = index.update_note(path)

    assert note.title == "改了标题"
    assert index.get("riji/weekly/2026-W26").title == "改了标题"


def test_search_finds_note_by_cjk_substring(index: JournalIndex) -> None:
    index.build_index()
    hits = index.search("架构设计")
    assert [h.source_id for h in hits] == ["riji/daily/2026-06-24"]


def test_search_can_exclude_private_notes(index: JournalIndex) -> None:
    index.build_index()
    assert index.search("私人想法") != []
    assert index.search("私人想法", include_private=False) == []


def test_private_flag_is_persisted(index: JournalIndex) -> None:
    index.build_index()
    note = index.get("riji/daily/2026-06-25")
    assert note is not None and note.private is True


def test_indexing_never_modifies_the_vault(index: JournalIndex, vault: Path) -> None:
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in vault.rglob("*.md")
    }

    index.build_index()
    index.search("架构设计")

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in vault.rglob("*.md")}
    assert before == after


def test_source_id_matches_index_keys(index: JournalIndex, vault: Path) -> None:
    index.build_index()
    expected = build_source_id(vault / "daily" / "2026-06-24.md", vault)
    assert index.get(expected) is not None
