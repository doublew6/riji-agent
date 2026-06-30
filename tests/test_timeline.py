from datetime import date
from pathlib import Path

import pytest

from riji_agent.journal.index import JournalIndex
from riji_agent.retrieval.errors import RetrievalError, RetrievalErrorCode
from riji_agent.retrieval.models import Granularity, RetrievalLimits, ToolContext
from riji_agent.retrieval.service import RetrievalService


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ctx(session: str = "s1") -> ToolContext:
    return ToolContext(request_id="r1", session_id=session, feishu_user_id="ou_1", persona_id="p1")


def _daily(root: Path, day: str, body: str, *, private: bool = False) -> None:
    front = f"date: {day}\n" + ("private: true\n" if private else "")
    _write(root / "daily" / f"{day}.md", f"---\n{front}---\n# {day}\n{body}\n")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    _daily(root, "2026-01-10", "项目进展顺利，完成初版。")
    _daily(root, "2026-02-15", "项目进展遇到阻碍。")
    _daily(root, "2026-03-20", "项目进展逐步恢复。")
    _daily(root, "2026-06-20", "今天只是无关的杂记。")
    _daily(root, "2026-06-24", "项目进展评审通过。")
    _daily(root, "2026-06-25", "项目进展的私密想法。", private=True)
    return root


def _service(tmp_path: Path, vault: Path, **limit_kwargs) -> RetrievalService:
    index = JournalIndex(database_path=tmp_path / "data" / "i.sqlite3", journal_root=vault)
    index.build_index()
    limits = RetrievalLimits(**limit_kwargs) if limit_kwargs else None
    return RetrievalService(index, limits=limits)


@pytest.fixture
def service(tmp_path: Path, vault: Path) -> RetrievalService:
    return _service(tmp_path, vault)


# ------------------------------------------------------------------- timeline

def test_timeline_groups_by_month(service: RetrievalService) -> None:
    result = service.timeline(
        _ctx(), "项目进展", date(2026, 1, 1), date(2026, 6, 30), Granularity.MONTH
    )
    labels = [b.period for b in result.buckets]
    assert labels == ["2026-01", "2026-02", "2026-03", "2026-06"]
    assert result.notes_found == 4  # private 2026-06-25 excluded
    assert result.insufficient_evidence is False


def test_timeline_reports_empty_periods_as_gaps(service: RetrievalService) -> None:
    result = service.timeline(
        _ctx(), "项目进展", date(2026, 1, 1), date(2026, 6, 30), Granularity.MONTH
    )
    assert "2026-04" in result.empty_periods
    assert "2026-05" in result.empty_periods


def test_timeline_excludes_private_notes(service: RetrievalService) -> None:
    result = service.timeline(
        _ctx(), "项目进展", date(2026, 6, 1), date(2026, 6, 30), Granularity.DAY
    )
    ids = {e.source_id for b in result.buckets for e in b.entries}
    assert "riji/daily/2026-06-25" not in ids


def test_timeline_flags_insufficient_evidence(service: RetrievalService) -> None:
    result = service.timeline(
        _ctx(), "完全不存在的主题词", date(2026, 1, 1), date(2026, 6, 30), Granularity.MONTH
    )
    assert result.notes_found == 0
    assert result.insufficient_evidence is True


def test_timeline_records_evidence_for_read_note(service: RetrievalService) -> None:
    ctx = _ctx()
    service.timeline(ctx, "项目进展", date(2026, 1, 1), date(2026, 6, 30), Granularity.MONTH)
    note = service.read_note(ctx, "riji/daily/2026-01-10")
    assert note.source_id == "riji/daily/2026-01-10"


def test_timeline_rejects_empty_topic(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.timeline(_ctx(), "  ", date(2026, 1, 1), date(2026, 6, 30))
    assert err.value.code is RetrievalErrorCode.INVALID_QUERY


def test_timeline_rejects_reversed_range(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.timeline(_ctx(), "项目进展", date(2026, 6, 30), date(2026, 1, 1))
    assert err.value.code is RetrievalErrorCode.INVALID_QUERY


def test_timeline_rejects_oversized_range(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError):
        service.timeline(_ctx(), "项目进展", date(2020, 1, 1), date(2026, 6, 30))


def test_timeline_truncates_when_over_hit_cap(tmp_path: Path, vault: Path) -> None:
    svc = _service(tmp_path, vault, timeline_max_hits=2)
    result = svc.timeline(
        _ctx(), "项目进展", date(2026, 1, 1), date(2026, 6, 30), Granularity.MONTH
    )
    assert result.truncated is True
    assert result.notes_found == 2


# ------------------------------------------------------------ find_before_after

def test_find_before_after_splits_around_pivot(service: RetrievalService) -> None:
    result = service.find_before_after(_ctx(), date(2026, 2, 15), days=40, topic="项目进展")
    assert [e.source_id for e in result.before] == ["riji/daily/2026-01-10"]
    assert [e.source_id for e in result.on] == ["riji/daily/2026-02-15"]
    assert [e.source_id for e in result.after] == ["riji/daily/2026-03-20"]


def test_find_before_after_without_topic_lists_window(service: RetrievalService) -> None:
    result = service.find_before_after(_ctx(), date(2026, 6, 24), days=5)
    ids = {e.source_id for e in (*result.before, *result.on, *result.after)}
    assert ids == {"riji/daily/2026-06-20", "riji/daily/2026-06-24"}  # private excluded


def test_find_before_after_flags_insufficient(service: RetrievalService) -> None:
    result = service.find_before_after(_ctx(), date(2030, 1, 1), days=5, topic="项目进展")
    assert result.notes_found == 0
    assert result.insufficient_evidence is True


def test_find_before_after_rejects_non_positive_days(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.find_before_after(_ctx(), date(2026, 6, 24), days=0)
    assert err.value.code is RetrievalErrorCode.INVALID_QUERY


def test_find_before_after_records_evidence(service: RetrievalService) -> None:
    ctx = _ctx()
    service.find_before_after(ctx, date(2026, 6, 24), days=5)
    note = service.read_note(ctx, "riji/daily/2026-06-20")
    assert note.source_id == "riji/daily/2026-06-20"
