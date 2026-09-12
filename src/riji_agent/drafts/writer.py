"""Atomic append into riji/daily/YYYY-MM-DD.md.

All section edits are computed in memory first; the file is only replaced if
every operation succeeds, via a temp file + os.replace, so a failure never
leaves a half-written note.
"""

from __future__ import annotations

import errno
import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path
from typing import Callable, Sequence, Tuple, TypeVar

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.template import (
    append_to_section,
    instantiate_daily,
    section_contains_entry,
)
from riji_agent.journal.parser import build_source_id

_T = TypeVar("_T")
_TRANSIENT_IO_ERRNOS = {
    errno.EAGAIN,
    errno.EBUSY,
    getattr(errno, "EDEADLK", errno.EAGAIN),
}
_UNSUPPORTED_SYNC_ERRNOS = {
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


@dataclass(frozen=True)
class WriteOutcome:
    path: Path
    source_id: str
    before_hash: str
    after_hash: str
    sections: Tuple[str, ...]
    new_file: bool


@dataclass(frozen=True)
class WritePolicy:
    io_attempts: int = 3
    io_delay_seconds: float = 0.2
    stability_checks: int = 3
    stability_delay_seconds: float = 0.5
    before_replace: Callable[[], None] | None = None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit_operations(
    journal_root: Path,
    target_date: Date,
    operations: Sequence[DraftOperation],
    *,
    policy: WritePolicy = WritePolicy(),
) -> WriteOutcome:
    if not operations:
        raise DraftError(DraftErrorCode.NO_OPERATIONS, "draft has no operations")

    daily_dir = journal_root / "daily"
    path = daily_dir / f"{target_date.isoformat()}.md"

    if path.exists():
        text = _retry_transient_io(
            lambda: path.read_text(encoding="utf-8"),
            attempts=policy.io_attempts,
            delay_seconds=policy.io_delay_seconds,
        )
        before_hash = _sha256(text)
        new_file = False
    else:
        template_path = journal_root / "templates" / "daily.md"
        if not template_path.is_file():
            raise DraftError(
                DraftErrorCode.TEMPLATE_NOT_FOUND, "daily template is missing"
            )
        template = _retry_transient_io(
            lambda: template_path.read_text(encoding="utf-8"),
            attempts=policy.io_attempts,
            delay_seconds=policy.io_delay_seconds,
        )
        text = instantiate_daily(template, target_date)
        before_hash = ""
        new_file = True

    text = _apply_missing_operations(text, operations)

    daily_dir.mkdir(parents=True, exist_ok=True)
    persisted_text = _persist_and_verify(
        path,
        text,
        operations=operations,
        policy=policy,
    )

    return WriteOutcome(
        path=path,
        source_id=build_source_id(path, journal_root),
        before_hash=before_hash,
        after_hash=_sha256(persisted_text),
        sections=tuple(operation.section for operation in operations),
        new_file=new_file,
    )


def verify_committed_operations(
    path: Path,
    operations: Sequence[DraftOperation],
    *,
    retry_attempts: int = 3,
    retry_delay_seconds: float = 0.2,
) -> bool:
    """Re-read a note and verify every committed patch in its target section."""
    try:
        text = _retry_transient_io(
            lambda: path.read_text(encoding="utf-8"),
            attempts=retry_attempts,
            delay_seconds=retry_delay_seconds,
        )
    except OSError:
        return False
    return all(
        section_contains_entry(text, operation.section, operation.journal_text)
        for operation in operations
    )


def _persist_and_verify(
    path: Path,
    text: str,
    *,
    operations: Sequence[DraftOperation],
    policy: WritePolicy,
) -> str:
    candidate = text
    remaining = max(1, policy.io_attempts)
    while remaining:
        _atomic_replace(path, candidate, policy)
        observed = _read_verified_text(path, policy)
        stable, observed = _observe_stability(
            path,
            observed,
            operations,
            policy,
        )
        if stable:
            return observed
        remaining -= 1
        if remaining <= 0:
            break
        candidate = _apply_missing_operations(observed, operations)
    raise DraftError(
        DraftErrorCode.WRITE_VERIFICATION_FAILED,
        "journal write did not remain stable during read-back verification",
    )


def _observe_stability(
    path: Path,
    observed: str,
    operations: Sequence[DraftOperation],
    policy: WritePolicy,
) -> tuple[bool, str]:
    current = observed
    checks = max(1, policy.stability_checks)
    for check in range(checks):
        if not _contains_operations(current, operations):
            return False, current
        if check + 1 >= checks:
            return True, current
        if policy.stability_delay_seconds > 0:
            time.sleep(policy.stability_delay_seconds)
        current = _read_verified_text(path, policy)
    return True, current


def _apply_missing_operations(
    text: str, operations: Sequence[DraftOperation]
) -> str:
    updated = text
    for operation in operations:
        if section_contains_entry(updated, operation.section, operation.journal_text):
            continue
        updated = append_to_section(updated, operation.section, operation.journal_text,
                                    bullet=operation.content_type == "personal_journal")
    return updated


def _contains_operations(
    text: str, operations: Sequence[DraftOperation]
) -> bool:
    return all(
        section_contains_entry(text, operation.section, operation.journal_text)
        for operation in operations
    )


def _atomic_replace(path: Path, text: str, policy: WritePolicy) -> None:
    tmp = path.parent / f"{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        _retry_transient_io(
            lambda: tmp.write_text(text, encoding="utf-8"),
            attempts=policy.io_attempts,
            delay_seconds=policy.io_delay_seconds,
        )
        _sync_file(tmp)
        def replace_current() -> None:
            if policy.before_replace is not None:
                policy.before_replace()
            os.replace(tmp, path)
        _retry_transient_io(
            replace_current,
            attempts=policy.io_attempts,
            delay_seconds=policy.io_delay_seconds,
        )
        _sync_directory(path.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_verified_text(path: Path, policy: WritePolicy) -> str:
    return _retry_transient_io(
        lambda: path.read_text(encoding="utf-8"),
        attempts=policy.io_attempts,
        delay_seconds=policy.io_delay_seconds,
    )


def _sync_file(path: Path) -> None:
    # Windows maps fsync to the CRT commit call, which requires a writable
    # descriptor even though syncing does not modify the file contents.
    with path.open("r+b") as handle:
        _retry_transient_io(
            lambda: os.fsync(handle.fileno()), attempts=3, delay_seconds=0.05
        )


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED_SYNC_ERRNOS:
            raise
    finally:
        os.close(descriptor)


def _retry_transient_io(
    operation: Callable[[], _T],
    *,
    attempts: int,
    delay_seconds: float,
) -> _T:
    remaining = max(1, attempts)
    while True:
        try:
            return operation()
        except OSError as exc:
            remaining -= 1
            if remaining <= 0 or not _is_transient_io_error(exc):
                raise
            if delay_seconds > 0:
                time.sleep(delay_seconds)


def _is_transient_io_error(exc: OSError) -> bool:
    return exc.errno in _TRANSIENT_IO_ERRNOS
