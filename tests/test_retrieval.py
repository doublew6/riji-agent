from datetime import date
from pathlib import Path

import pytest

from riji_agent.journal.index import JournalIndex
from riji_agent.journal.models import NoteKind
from riji_agent.retrieval.errors import RetrievalError, RetrievalErrorCode
from riji_agent.retrieval.models import RetrievalLimits, ToolContext
from riji_agent.retrieval.schemas import TOOL_DEFINITIONS
from riji_agent.retrieval.service import RetrievalService


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ctx(session: str = "s1", request: str = "r1") -> ToolContext:
    return ToolContext(
        request_id=request, session_id=session, feishu_user_id="ou_1", persona_id="p1"
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    _write(
        root / "daily" / "2026-06-24.md",
        "---\ndate: 2026-06-24\ntags: [ai]\n---\n# 2026-06-24\n梳理了架构的记录与索引。\n",
    )
    _write(
        root / "daily" / "2026-06-20.md",
        "---\ndate: 2026-06-20\ntags: [trip]\n---\n# 2026-06-20\n出差旅行的记录与总结。\n",
    )
    _write(
        root / "daily" / "2026-06-25.md",
        "---\ndate: 2026-06-25\nprivate: true\n---\n# 2026-06-25\n机密的私人想法记录。\n",
    )
    _write(root / "weekly" / "2026-W26.md", "---\ntitle: 本周复盘\n---\n本周完成了骨架。\n")
    return root


@pytest.fixture
def service(tmp_path: Path, vault: Path) -> RetrievalService:
    index = JournalIndex(database_path=tmp_path / "data" / "idx.sqlite3", journal_root=vault)
    index.build_index()
    svc = RetrievalService(index)
    yield svc
    index.close()


# --------------------------------------------------------------- search_journal

def test_search_returns_items_with_stable_source_ids(service: RetrievalService) -> None:
    result = service.search_journal(_ctx(), "架构的记录")
    assert [i.source_id for i in result.items] == ["riji/daily/2026-06-24"]
    assert result.request_id == "r1"


def test_search_never_returns_private_notes(service: RetrievalService) -> None:
    result = service.search_journal(_ctx(), "私人想法")
    assert result.items == ()


def test_search_filters_by_date_range(service: RetrievalService) -> None:
    result = service.search_journal(_ctx(), "的记录", date_from=date(2026, 6, 22))
    assert [i.source_id for i in result.items] == ["riji/daily/2026-06-24"]


def test_search_filters_by_tags(service: RetrievalService) -> None:
    result = service.search_journal(_ctx(), "的记录", tags=["trip"])
    assert [i.source_id for i in result.items] == ["riji/daily/2026-06-20"]


def test_empty_query_is_rejected(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.search_journal(_ctx(), "   ")
    assert err.value.code is RetrievalErrorCode.INVALID_QUERY


def test_malformed_fts_query_is_safe(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.search_journal(_ctx(), '"')
    assert err.value.code is RetrievalErrorCode.INVALID_QUERY


def test_top_k_is_clamped_to_the_maximum(tmp_path: Path, vault: Path) -> None:
    index = JournalIndex(database_path=tmp_path / "d" / "i.sqlite3", journal_root=vault)
    index.build_index()
    svc = RetrievalService(index, limits=RetrievalLimits(max_top_k=1))
    result = svc.search_journal(_ctx(), "的记录", top_k=999)
    assert len(result.items) == 1
    index.close()


def test_total_snippet_length_is_capped(tmp_path: Path, vault: Path) -> None:
    index = JournalIndex(database_path=tmp_path / "d" / "i.sqlite3", journal_root=vault)
    index.build_index()
    svc = RetrievalService(index, limits=RetrievalLimits(max_total_snippet_chars=1))
    result = svc.search_journal(_ctx(), "的记录")
    assert result.truncated is True
    assert len(result.items) == 0


# --------------------------------------------------------------- read_note gate

def test_read_note_requires_prior_search_evidence(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.read_note(_ctx(), "riji/daily/2026-06-24")
    assert err.value.code is RetrievalErrorCode.NO_EVIDENCE


def test_read_note_succeeds_after_search(service: RetrievalService) -> None:
    ctx = _ctx()
    service.search_journal(ctx, "架构的记录")
    note = service.read_note(ctx, "riji/daily/2026-06-24")
    assert note.source_id == "riji/daily/2026-06-24"
    assert "架构" in note.body


def test_evidence_is_scoped_per_session(service: RetrievalService) -> None:
    service.search_journal(_ctx(session="a"), "架构的记录")
    with pytest.raises(RetrievalError) as err:
        service.read_note(_ctx(session="b"), "riji/daily/2026-06-24")
    assert err.value.code is RetrievalErrorCode.NO_EVIDENCE


def test_read_note_rejects_filesystem_paths(service: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as err:
        service.read_note(_ctx(), "/etc/passwd")
    assert err.value.code is RetrievalErrorCode.NO_EVIDENCE


def test_read_note_not_found_after_source_removed(service: RetrievalService) -> None:
    ctx = _ctx()
    service.search_journal(ctx, "架构的记录")
    service._index.remove_source("riji/daily/2026-06-24")
    with pytest.raises(RetrievalError) as err:
        service.read_note(ctx, "riji/daily/2026-06-24")
    assert err.value.code is RetrievalErrorCode.NOT_FOUND


def test_read_note_blocks_private_even_with_evidence(service: RetrievalService) -> None:
    ctx = _ctx()
    # Defence in depth: even if a private id were somehow in the evidence set.
    service._evidence[ctx.session_id] = {"riji/daily/2026-06-25"}
    with pytest.raises(RetrievalError) as err:
        service.read_note(ctx, "riji/daily/2026-06-25")
    assert err.value.code is RetrievalErrorCode.PRIVATE_BLOCKED


def test_read_note_body_is_truncated(tmp_path: Path, vault: Path) -> None:
    index = JournalIndex(database_path=tmp_path / "d" / "i.sqlite3", journal_root=vault)
    index.build_index()
    svc = RetrievalService(index, limits=RetrievalLimits(read_note_max_chars=3))
    ctx = _ctx()
    svc.search_journal(ctx, "架构的记录")
    note = svc.read_note(ctx, "riji/daily/2026-06-24")
    assert note.truncated is True
    assert len(note.body) == 3
    index.close()


# --------------------------------------------------------------- list_periods

def test_list_periods_returns_metadata_excluding_private(service: RetrievalService) -> None:
    result = service.list_periods(_ctx())
    ids = {i.source_id for i in result.items}
    assert "riji/daily/2026-06-25" not in ids  # private excluded
    assert "riji/weekly/2026-W26" in ids


def test_list_periods_filters_by_kind(service: RetrievalService) -> None:
    result = service.list_periods(_ctx(), kind=NoteKind.WEEKLY)
    assert {i.kind for i in result.items} == {NoteKind.WEEKLY}


def test_list_periods_filters_by_date(service: RetrievalService) -> None:
    result = service.list_periods(_ctx(), date_from=date(2026, 6, 24), date_to=date(2026, 6, 24))
    assert [i.source_id for i in result.items] == ["riji/daily/2026-06-24"]


# ------------------------------------------------------------------- schemas

def test_tool_definitions_cover_all_tools() -> None:
    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert names == {
        "search_journal",
        "read_note",
        "list_periods",
        "timeline",
        "find_before_after",
    }


def test_read_note_schema_exposes_only_source_id() -> None:
    read = next(t for t in TOOL_DEFINITIONS if t["name"] == "read_note")
    props = read["parameters"]["properties"]
    assert set(props) == {"source_id"}
    assert read["parameters"]["required"] == ["source_id"]
