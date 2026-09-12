"""A scoped execution heartbeat; never retry or outlive a generation call."""

from __future__ import annotations

import threading
from typing import Callable

from riji_agent.mentors.models import MentorError

LEASE_TTL_SECONDS = 180
HEARTBEAT_INTERVAL_SECONDS = 30.0


class ExecutionHeartbeat:
    def __init__(self, renew: Callable[[threading.Event], None]) -> None:
        self._renew = renew
        self._done = threading.Event()
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="riji-mentor-heartbeat", daemon=True)

    def __enter__(self) -> ExecutionHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, error_type, error, traceback) -> None:
        self._done.set()
        self._thread.join()
        if error_type is None:
            self.check()

    def check(self) -> None:
        if self._error is not None:
            raise MentorError(self._error)

    def _run(self) -> None:
        while not self._done.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                self._renew(self._done)
            except MentorError as error:
                self._error = error.code
                return
            except Exception:
                self._error = "execution_heartbeat_failed"
                return
