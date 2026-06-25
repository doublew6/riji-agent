import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.models import DraftOperation, DraftStatus
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.journal.index import JournalIndex

TEMPLATE = "# {{date}}\n\n## 🌆 Evening\n\n## 🧠 Notes\n"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    return root


@pytest.fixture
def setup(tmp_path: Path):
    root = _vault(tmp_path)
    index = JournalIndex(database_path=tmp_path / "data" / "idx.sqlite3", journal_root=root)
    store = DraftStore(tmp_path / "data" / "drafts.sqlite3")
    clock = Clock(datetime(2026, 6, 25, 8, 0, tzinfo=timezone.utc))
    service = DraftService(store, root, index, ttl_minutes=30, now=clock)
    yield service, store, index, root, clock
    index.close()
    store.close()


def _ops():
    return [DraftOperation("🌆 Evening", "评审通过")]


def _create(service):
    return service.create_draft(
        user_id="u1", session_id="u1:gentle:c1", persona_id="gentle", operations=_ops()
    )


def test_create_then_commit_writes_and_indexes(setup) -> None:
    service, _store, index, root, _clock = setup
    preview = _create(service)
    result = service.commit_draft(preview.draft_id, user_id="u1", token=preview.token)

    assert result.source_id == "riji/daily/2026-06-25"
    assert "- 评审通过" in (root / "daily" / "2026-06-25.md").read_text(encoding="utf-8")
    assert index.get("riji/daily/2026-06-25") is not None  # incremental index ran


def test_unconfirmed_draft_never_writes(setup) -> None:
    service, _store, _index, root, _clock = setup
    _create(service)  # no commit
    assert not (root / "daily" / "2026-06-25.md").exists()


def test_expired_confirmation_is_rejected(setup) -> None:
    service, store, _index, root, clock = setup
    preview = _create(service)
    clock.now += timedelta(minutes=31)
    with pytest.raises(DraftError) as err:
        service.commit_draft(preview.draft_id, user_id="u1", token=preview.token)
    assert err.value.code is DraftErrorCode.TOKEN_EXPIRED
    assert not (root / "daily" / "2026-06-25.md").exists()
    assert store.get(preview.draft_id).status is DraftStatus.EXPIRED


def test_other_user_cannot_confirm(setup) -> None:
    service, _store, _index, root, _clock = setup
    preview = _create(service)
    with pytest.raises(DraftError) as err:
        service.commit_draft(preview.draft_id, user_id="someone_else", token=preview.token)
    assert err.value.code is DraftErrorCode.WRONG_USER
    assert not (root / "daily" / "2026-06-25.md").exists()


def test_bad_token_is_rejected(setup) -> None:
    service, _store, _index, _root, _clock = setup
    preview = _create(service)
    with pytest.raises(DraftError) as err:
        service.commit_draft(preview.draft_id, user_id="u1", token="wrong")
    assert err.value.code is DraftErrorCode.TOKEN_INVALID


def test_duplicate_confirmation_does_not_write_twice(setup) -> None:
    service, _store, _index, root, _clock = setup
    preview = _create(service)
    service.commit_draft(preview.draft_id, user_id="u1", token=preview.token)
    with pytest.raises(DraftError) as err:
        service.commit_draft(preview.draft_id, user_id="u1", token=preview.token)
    assert err.value.code is DraftErrorCode.NOT_AWAITING
    text = (root / "daily" / "2026-06-25.md").read_text(encoding="utf-8")
    assert text.count("- 评审通过") == 1  # single-use token prevents double write


def test_missing_section_keeps_draft_awaiting(setup) -> None:
    service, store, _index, root, _clock = setup
    preview = service.create_draft(
        user_id="u1", session_id="s", persona_id="gentle",
        operations=[DraftOperation("不存在的区块", "x")],
    )
    with pytest.raises(DraftError) as err:
        service.commit_draft(preview.draft_id, user_id="u1", token=preview.token)
    assert err.value.code is DraftErrorCode.SECTION_NOT_FOUND
    assert not (root / "daily" / "2026-06-25.md").exists()
    assert store.get(preview.draft_id).status is DraftStatus.AWAITING  # can retry


def test_unknown_draft_raises(setup) -> None:
    service, _store, _index, _root, _clock = setup
    with pytest.raises(DraftError) as err:
        service.commit_draft("nope", user_id="u1")
    assert err.value.code is DraftErrorCode.DRAFT_NOT_FOUND


def test_claim_for_commit_is_won_by_only_one_connection(setup, tmp_path) -> None:
    service, _store, _index, _root, _clock = setup
    preview = _create(service)
    db = tmp_path / "data" / "drafts.sqlite3"
    # Two independent connections to the same DB file model two workers.
    worker_a = DraftStore(db)
    worker_b = DraftStore(db)
    try:
        assert worker_a.claim_for_commit(preview.draft_id) is True
        assert worker_b.claim_for_commit(preview.draft_id) is False  # already taken
        assert worker_b.get(preview.draft_id).status is DraftStatus.COMMITTING
    finally:
        worker_a.close()
        worker_b.close()


def test_concurrent_workers_commit_exactly_once(tmp_path) -> None:
    # Separate DraftService instances on a shared DB file race to confirm the
    # same draft; the DB-level claim must let exactly one write.
    root = _vault(tmp_path)
    db = tmp_path / "data" / "drafts.sqlite3"
    clock = Clock(datetime(2026, 6, 25, 8, 0, tzinfo=timezone.utc))

    def make_service(tag: str) -> DraftService:
        index = JournalIndex(
            database_path=tmp_path / "data" / f"idx-{tag}.sqlite3", journal_root=root
        )
        return DraftService(DraftStore(db), root, index, ttl_minutes=30, now=clock)

    preview = make_service("creator").create_draft(
        user_id="u1", session_id="s", persona_id="gentle", operations=_ops()
    )

    n = 5
    barrier = threading.Barrier(n)
    lock = threading.Lock()
    results, errors = [], []

    def worker(tag: str) -> None:
        service = make_service(tag)
        barrier.wait()  # maximise overlap on the claim
        try:
            result = service.commit_draft(preview.draft_id, user_id="u1", token=preview.token)
            with lock:
                results.append(result)
        except DraftError as exc:
            with lock:
                errors.append(exc.code)

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1  # exactly one writer won
    assert errors == [DraftErrorCode.NOT_AWAITING] * (n - 1)
    text = (root / "daily" / "2026-06-25.md").read_text(encoding="utf-8")
    assert text.count("- 评审通过") == 1  # no double append under concurrency
