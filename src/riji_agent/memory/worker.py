"""Daemon worker for durable memory capture and snapshot retries."""

from __future__ import annotations

import threading
import logging
from typing import Optional

from riji_agent.memory.service import CaptureProcessor
from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.memory.journal_engine import JournalMemoryEngine


class MemoryWorker:
    def __init__(self, processor: CaptureProcessor, *, interval_seconds: float = 2.0,
                 organizer: Optional[MemoryOrganizer] = None,
                 journal: Optional[JournalMemoryEngine] = None) -> None:
        self._processor = processor
        self._organizer = organizer
        self.journal = journal
        self._turn = 0
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="riji-memory-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> bool:
        processors = [item for item in (self._processor, self.journal, self._organizer) if item is not None]
        start = self._turn % len(processors)
        self._turn += 1
        for processor in processors[start:] + processors[:start]:
            if processor.process_next():
                return True
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception:
                logging.getLogger("riji_agent.memory").warning("memory worker cycle failed; retrying")
                processed = False
            if processed:
                continue
            self._wake.wait(self._interval)
            self._wake.clear()
