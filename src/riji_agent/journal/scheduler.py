"""Background journal indexing: prewarm, periodic refresh and observable status.

The scheduler only ever calls the index's own ``build_index`` (content-hash
incremental), so it inherits the read-only-over-the-vault guarantee. It never
logs note bodies, paths or credentials — only counts, timings and a sanitized
error class. A reentrancy guard means a slow run is never overlapped by the
next tick or a manual prewarm.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from riji_agent.journal.index import IndexStats, JournalIndex

_LOGGER = logging.getLogger("riji_agent.index")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: BaseException) -> str:
    """A non-leaking error label: the exception class name only.

    Exception messages may carry vault paths or note text, so they are never
    surfaced in status or logs; the class name is enough to act on.
    """
    return type(exc).__name__


class IndexScheduler:
    """Runs incremental indexing once, on demand, and on a fixed interval."""

    def __init__(
        self,
        index: JournalIndex,
        *,
        interval_seconds: int = 600,
        enabled: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._index = index
        self._interval = max(1, int(interval_seconds))
        self._enabled = enabled
        self._logger = logger or _LOGGER

        self._run_lock = threading.Lock()  # reentrancy guard for a single run
        self._state_lock = threading.Lock()  # guards the status fields below
        self._stop = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None

        self._running = False
        self._last_started_at: Optional[str] = None
        self._last_finished_at: Optional[str] = None
        self._last_duration_seconds: Optional[float] = None
        self._last_stats: Optional[Dict[str, int]] = None
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------ running

    def run_once(self, *, rebuild: bool = False) -> Optional[IndexStats]:
        """Run one incremental (or full) index. Skips if one is already running.

        Returns the stats on success, or None if skipped (reentrancy) or failed
        (the error is recorded in status, never raised, so a bad run cannot
        crash the service).
        """
        if not self._run_lock.acquire(blocking=False):
            self._logger.debug("index run skipped: a previous run is still in progress")
            return None
        with self._state_lock:
            self._running = True
            self._last_started_at = _utcnow_iso()
        started = time.monotonic()
        stats: Optional[IndexStats] = None
        error: Optional[str] = None
        try:
            stats = self._index.build_index(rebuild=rebuild)
        except Exception as exc:  # never let an index failure escape
            error = _safe_error(exc)
            self._logger.warning("index run failed: %s", error)
        finally:
            duration = round(time.monotonic() - started, 3)
            with self._state_lock:
                self._running = False
                self._last_finished_at = _utcnow_iso()
                self._last_duration_seconds = duration
                self._last_error = error
                if stats is not None:
                    self._last_stats = {
                        "added": stats.added,
                        "updated": stats.updated,
                        "unchanged": stats.unchanged,
                        "deleted": stats.deleted,
                        "skipped": stats.skipped,
                    }
            self._run_lock.release()
        return stats

    def begin_prewarm(self) -> threading.Event:
        """Start the initial index in a daemon thread; return a 'done' Event.

        The worker is a daemon so a build stuck on a cold file never keeps the
        process alive after shutdown; callers await the returned event without
        blocking. This is what the app lifespan uses for non-blocking startup.
        """
        done = threading.Event()

        def _run() -> None:
            try:
                self.run_once()
            finally:
                done.set()

        threading.Thread(target=_run, name="riji-index-prewarm", daemon=True).start()
        return done

    def prewarm(self, timeout: Optional[float] = None) -> bool:
        """Kick the initial index in the background; wait up to ``timeout``.

        Returns True if the first run completed within the timeout, False if it
        is still running (so startup proceeds without unbounded blocking on a
        cold vault). ``timeout=None`` waits for completion.
        """
        return self.begin_prewarm().wait(timeout)

    def start(self) -> None:
        """Start the periodic refresh loop (no-op if disabled or already on)."""
        if not self._enabled or self._loop_thread is not None:
            return
        self._stop.clear()
        self._loop_thread = threading.Thread(target=self._loop, name="riji-index", daemon=True)
        self._loop_thread.start()

    def _loop(self) -> None:
        # Wait one interval before the first scheduled run; prewarm covers t=0.
        while not self._stop.wait(self._interval):
            self.run_once()

    def stop(self) -> None:
        self._stop.set()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)
            self._loop_thread = None

    # ------------------------------------------------------------------ status

    def status(self) -> Dict[str, Any]:
        """Metadata only — never note bodies, file lists or credentials."""
        note_count = self._index.count()
        last_indexed_at = self._index.last_indexed_at()
        with self._state_lock:
            return {
                "note_count": note_count,
                "last_indexed_at": last_indexed_at,
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                "last_duration_seconds": self._last_duration_seconds,
                "last_stats": dict(self._last_stats) if self._last_stats is not None else None,
                "last_error": self._last_error,
                "running": self._running,
                "schedule_enabled": self._enabled,
                "interval_seconds": self._interval,
            }
