"""Resilient indexing (issue #42): per-file read budget, skip, progress.

A cold iCloud file can block in ``read_bytes()``. Indexing must time it out,
skip it (without deleting its prior entry), keep going, and stay observable.
"""

import time
from pathlib import Path

import pytest

from riji_agent.journal import index as index_mod
from riji_agent.journal import parser
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.parser import SlowFileError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    _write(root / "daily" / "2026-06-24.md", "---\ndate: 2026-06-24\n---\n# A\n甲。\n")
    _write(root / "daily" / "2026-06-25.md", "---\ndate: 2026-06-25\n---\n# B\n乙。\n")
    return root


def _index(tmp_path: Path, root: Path, *, timeout=0.5) -> JournalIndex:
    return JournalIndex(
        database_path=tmp_path / "d" / "i.sqlite3", journal_root=root, file_read_timeout=timeout
    )


def _cold(target_name: str):
    def reader(path: Path, timeout):
        if path.name == target_name:
            raise SlowFileError("simulated cold file")
        return path.read_bytes()

    return reader


# --- parser-level read budget --------------------------------------------------

def test_read_file_bytes_times_out_without_waiting_for_the_slow_read(monkeypatch, tmp_path) -> None:
    note = tmp_path / "x.md"
    note.write_text("hi", encoding="utf-8")

    def slow_read(self) -> bytes:
        time.sleep(5)  # far longer than the budget; the test never waits for it
        return b"hi"

    monkeypatch.setattr(Path, "read_bytes", slow_read)
    started = time.monotonic()
    with pytest.raises(SlowFileError):
        parser.read_file_bytes(note, timeout=0.1)
    assert time.monotonic() - started < 2  # gave up at the budget, didn't block 5s


def test_read_file_bytes_no_timeout_reads_directly(tmp_path) -> None:
    note = tmp_path / "x.md"
    note.write_text("hello", encoding="utf-8")
    assert parser.read_file_bytes(note, timeout=None) == b"hello"


# --- index-level skip / preserve / progress -----------------------------------

def test_slow_file_is_skipped_and_others_still_index(tmp_path, monkeypatch) -> None:
    root = _vault(tmp_path)
    idx = _index(tmp_path, root)
    monkeypatch.setattr(index_mod, "read_file_bytes", _cold("2026-06-25.md"))

    stats = idx.build_index()
    assert stats.skipped == 1
    assert stats.added == 1
    assert stats.skipped_sources == ["riji/daily/2026-06-25"]  # sanitized id only
    assert idx.count() == 1
    assert idx.get("riji/daily/2026-06-25") is None
    assert idx.get("riji/daily/2026-06-24") is not None
    idx.close()


def test_skipped_file_keeps_its_existing_entry(tmp_path, monkeypatch) -> None:
    root = _vault(tmp_path)
    idx = _index(tmp_path, root)
    idx.build_index()  # both indexed while files are readable
    assert idx.count() == 2

    monkeypatch.setattr(index_mod, "read_file_bytes", _cold("2026-06-25.md"))
    stats = idx.build_index()  # now one file goes cold
    assert stats.skipped == 1
    assert stats.deleted == 0  # a skipped file is NOT treated as deleted
    assert idx.count() == 2  # its prior entry is preserved
    assert idx.get("riji/daily/2026-06-25") is not None
    idx.close()


def test_partial_run_finishes_and_reports_progress(tmp_path) -> None:
    root = _vault(tmp_path)
    idx = _index(tmp_path, root, timeout=None)
    events = []
    stats = idx.build_index(progress=lambda d, t, a, s: events.append((d, t, a, s)))

    assert len(events) == 2  # one progress call per file
    assert [e[0] for e in events] == [1, 2]  # done counter advances
    assert all(e[1] == 2 for e in events)  # total reported
    assert {e[2] for e in events} == {"added"}
    assert stats.added == 2
    idx.close()
