from pathlib import Path

import pytest

from riji_agent.memory.models import CandidateStatus
from riji_agent.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(database_path=tmp_path / "data" / "mem.sqlite3")
    yield s
    s.close()


def test_candidate_is_private_to_its_persona(store: MemoryStore) -> None:
    store.add_candidate("u1", "gentle_reviewer", "用户喜欢晨跑")
    assert len(store.list_candidates("u1", "gentle_reviewer")) == 1
    assert store.list_candidates("u1", "blunt_coach") == []


def test_unconfirmed_candidate_is_not_shared(store: MemoryStore) -> None:
    store.add_candidate("u1", "gentle_reviewer", "尚未确认的观察")
    assert store.list_confirmed_memories("u1") == []


def test_confirming_candidate_makes_it_shared(store: MemoryStore) -> None:
    cid = store.add_candidate("u1", "gentle_reviewer", "用户在准备考试")
    store.confirm_candidate(cid)

    confirmed = store.list_confirmed_memories("u1")
    assert [m.content for m in confirmed] == ["用户在准备考试"]
    pending = store.list_candidates("u1", "gentle_reviewer", status=CandidateStatus.PENDING)
    assert pending == []


def test_rejecting_candidate_keeps_it_out_of_shared(store: MemoryStore) -> None:
    cid = store.add_candidate("u1", "blunt_coach", "错误观察")
    store.reject_candidate(cid)
    assert store.list_confirmed_memories("u1") == []
    rejected = store.list_candidates("u1", "blunt_coach", status=CandidateStatus.REJECTED)
    assert len(rejected) == 1


def test_confirm_unknown_candidate_raises(store: MemoryStore) -> None:
    with pytest.raises(KeyError):
        store.confirm_candidate(999)


def test_preferences_are_shared_and_upserted(store: MemoryStore) -> None:
    store.set_preference("u1", "tone", "warm")
    store.set_preference("u1", "tone", "blunt")  # upsert
    assert store.get_preferences("u1") == {"tone": "blunt"}


def test_session_history_is_isolated_per_persona(store: MemoryStore) -> None:
    store.append_message("u1", "gentle_reviewer", "c1", "user", "只跟温柔回顾者说的话")
    assert len(store.get_session_history("u1", "gentle_reviewer", "c1")) == 1
    # switching persona must not leak the other persona's history
    assert store.get_session_history("u1", "blunt_coach", "c1") == []
