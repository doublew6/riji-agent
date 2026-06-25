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
