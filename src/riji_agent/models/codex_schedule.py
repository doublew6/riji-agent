"""Shared local Codex capacity: foreground requests precede queued memory jobs."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time
from typing import Iterator

from riji_agent.models.types import LLMError


class CodexScheduler:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._busy = False
        self._chat_waiters = 0
        self._blocked_until = 0.0
        self._blocked_reason = "codex_unavailable"

    @contextmanager
    def acquire(self, purpose: str, deadline: float) -> Iterator[None]:
        chat = purpose == "chat"
        with self._condition:
            self._chat_waiters += int(chat)
            try:
                while True:
                    now = time.monotonic()
                    if now < self._blocked_until:
                        raise LLMError(self._blocked_reason)
                    if now >= deadline:
                        raise LLMError("codex_queue_timeout")
                    if not self._busy and (chat or not self._chat_waiters):
                        self._busy = True
                        break
                    self._condition.wait(deadline - now)
            finally:
                self._chat_waiters -= int(chat)
        try:
            yield
        except LLMError as exc:
            delays = {"codex_quota_exhausted": 900, "codex_login_required": 300}
            if str(exc) in delays:
                with self._condition:
                    self._blocked_until = time.monotonic() + delays[str(exc)]
                    self._blocked_reason = str(exc)
            raise
        finally:
            with self._condition:
                self._busy = False
                self._condition.notify_all()


SCHEDULER = CodexScheduler()
