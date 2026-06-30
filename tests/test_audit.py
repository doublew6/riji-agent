from pathlib import Path

import pytest

from riji_agent.audit.store import AuditStore


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "audit.sqlite3")
    yield s
    s.close()


def test_records_metadata_only(store: AuditStore) -> None:
    store.record(
        request_id="r1", persona_id="gentle_reviewer", feishu_user_id="ou_1",
        tool="search_journal", ok=True, error=None, source_ids=["riji/daily/2026-06-24"],
    )
    events = store.list_for_request("r1")
    assert len(events) == 1
    assert events[0].tool == "search_journal"
    assert events[0].source_ids == ("riji/daily/2026-06-24",)


def test_all_source_ids_aggregates(store: AuditStore) -> None:
    store.record(request_id="r1", persona_id="p", feishu_user_id="u", tool="search_journal",
                 ok=True, error=None, source_ids=["a", "b"])
    store.record(request_id="r1", persona_id="p", feishu_user_id="u", tool="read_note",
                 ok=True, error=None, source_ids=["c"])
    assert set(store.all_source_ids()) == {"a", "b", "c"}


def test_error_status_is_recorded(store: AuditStore) -> None:
    store.record(request_id="r1", persona_id="p", feishu_user_id="u", tool="read_note",
                 ok=False, error="no_evidence", source_ids=[])
    assert store.all()[0].ok is False
    assert store.all()[0].error == "no_evidence"
