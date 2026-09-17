"""Cooperative process-wide mutation boundary for riji-agent memory writers.

This is not provider CAS against direct third-party Mem0 administration. Every
riji-agent process serving this runtime must use the same private lock path.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager, nullcontext
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from riji_agent.memory.backend import MemoryBackendError

_LOCKS: dict[str, tuple[threading.RLock, threading.local]] = {}
_REGISTRY_LOCK = threading.Lock()
_MUTATORS = frozenset({"add", "add_explicit", "update", "delete", "purge"})


def serialize_mutation(backend_attribute: str = "backend") -> Callable:
    """Lock the complete read/modify/write, when the runtime enables a guard.

    Raw update serialization alone cannot protect metadata read by its caller.
    Keep unguarded, nonmobile backends compatible with existing callers.
    """
    def decorate(method: Callable) -> Callable:
        @wraps(method)
        def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
            backend = getattr(self, backend_attribute)
            guard = getattr(backend, "mutation_guard", None)
            with guard() if guard is not None else nullcontext():
                return method(self, *args, **kwargs)
        return guarded
    return decorate


class SerializedMemoryBackend:
    def __init__(self, backend, path: Path) -> None:
        self.backend, self.path = backend, path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _REGISTRY_LOCK:
            self._lock, self._local = _LOCKS.setdefault(str(path.resolve()), (threading.RLock(), threading.local()))

    @contextmanager
    def mutation_guard(self) -> Iterator[None]:
        import fcntl
        with self._lock:
            if getattr(self._local, "active", False):
                yield
                return
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            except OSError:
                raise MemoryBackendError("memory_mutation_guard_unavailable") from None
            with os.fdopen(fd, "a") as handle:
                os.fchmod(handle.fileno(), 0o600)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._local.active = True
                try:
                    yield
                finally:
                    self._local.active = False
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __getattr__(self, name: str):
        value = getattr(self.backend, name)
        if name not in _MUTATORS or not callable(value):
            return value

        @wraps(value)
        def mutate(*args, **kwargs):
            with self.mutation_guard():
                return value(*args, **kwargs)
        return mutate
