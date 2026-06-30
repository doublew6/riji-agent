"""IndexScheduler: reentrancy, bounded prewarm, failure degradation, status.

A fake index lets these tests control timing and failures deterministically
without touching SQLite or the vault.
"""

import threading
import time

from riji_agent.journal.index import IndexStats
from riji_agent.journal.scheduler import IndexScheduler


class FakeIndex:
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.block: threading.Event | None = None
        self.raise_exc: BaseException | None = None
        self.notes = 3

    def build_index(self, *, rebuild: bool = False) -> IndexStats:
        self.calls += 1
        self.started.set()
        if self.block is not None:
            self.block.wait(2)
        if self.raise_exc is not None:
            raise self.raise_exc
        return IndexStats(added=1, updated=0, unchanged=2, deleted=0)

    def count(self) -> int:
        return self.notes

    def last_indexed_at(self):
        return "2026-06-25T00:00:00+00:00"


def test_run_once_records_status_on_success() -> None:
    sched = IndexScheduler(FakeIndex(), interval_seconds=123, enabled=True)
    stats = sched.run_once()
    assert stats == IndexStats(added=1, updated=0, unchanged=2, deleted=0)

    status = sched.status()
    assert status["note_count"] == 3
    assert status["last_indexed_at"] == "2026-06-25T00:00:00+00:00"
    assert status["last_stats"] == {
        "added": 1, "updated": 0, "unchanged": 2, "deleted": 0, "skipped": 0
    }
    assert status["last_error"] is None
    assert status["running"] is False
    assert status["schedule_enabled"] is True
    assert status["interval_seconds"] == 123
    assert status["last_duration_seconds"] is not None


def test_run_once_is_reentrancy_guarded() -> None:
    index = FakeIndex()
    index.block = threading.Event()
    sched = IndexScheduler(index, enabled=False)

    worker = threading.Thread(target=sched.run_once)
    worker.start()
    assert index.started.wait(1)  # first run is inside build_index, holding the guard

    assert sched.run_once() is None  # second run is skipped, not run concurrently
    assert index.calls == 1

    index.block.set()
    worker.join(2)
    assert index.calls == 1  # only the first run executed


def test_failed_run_is_safe_and_recoverable() -> None:
    index = FakeIndex()
    index.raise_exc = ValueError("/private/vault/2026-06-25.md 私密正文")
    sched = IndexScheduler(index, enabled=False)

    assert sched.run_once() is None  # failure does not raise out of the scheduler
    status = sched.status()
    assert status["last_error"] == "ValueError"  # class name only
    assert "/private" not in str(status)  # no path/body leak
    assert "私密正文" not in str(status)
    assert status["running"] is False

    index.raise_exc = None  # a later good run clears the error
    sched.run_once()
    assert sched.status()["last_error"] is None


def test_prewarm_does_not_block_unboundedly() -> None:
    index = FakeIndex()
    index.block = threading.Event()
    sched = IndexScheduler(index, enabled=False)

    completed = sched.prewarm(timeout=0.05)
    assert completed is False  # still indexing; startup proceeds
    assert sched.status()["running"] is True

    index.block.set()
    for _ in range(200):
        if not sched.status()["running"]:
            break
        time.sleep(0.01)
    assert sched.status()["running"] is False


def test_disabled_scheduler_does_not_start_loop() -> None:
    sched = IndexScheduler(FakeIndex(), enabled=False)
    sched.start()
    assert sched._loop_thread is None
    sched.stop()  # safe no-op
