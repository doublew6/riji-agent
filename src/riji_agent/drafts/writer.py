"""Atomic append into riji/daily/YYYY-MM-DD.md.

All section edits are computed in memory first; the file is only replaced if
every operation succeeds, via a temp file + os.replace, so a failure never
leaves a half-written note.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
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
from riji_agent.media.models import MediaAttachment

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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit_operations(
    journal_root: Path,
    target_date: Date,
    operations: Sequence[DraftOperation],
    *,
    attachments: Sequence[MediaAttachment] = (),
    retry_attempts: int = 3,
    retry_delay_seconds: float = 0.2,
) -> WriteOutcome:
    if not operations:
        raise DraftError(DraftErrorCode.NO_OPERATIONS, "draft has no operations")

    daily_dir = journal_root / "daily"
    path = daily_dir / f"{target_date.isoformat()}.md"

    if path.exists():
        text = _retry_transient_io(
            lambda: path.read_text(encoding="utf-8"),
            attempts=retry_attempts,
            delay_seconds=retry_delay_seconds,
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
            attempts=retry_attempts,
            delay_seconds=retry_delay_seconds,
        )
        text = instantiate_daily(template, target_date)
        before_hash = ""
        new_file = True

    rendered_operations = _render_attachments(operations, attachments)
    sections = []
    for operation in rendered_operations:
        text = append_to_section(
            text, operation.section, operation.content
        )  # may raise
        sections.append(operation.section)

    daily_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp-{uuid.uuid4().hex}"
    staged_assets, created_assets = _prepare_assets(journal_root, attachments)
    try:
        _retry_transient_io(
            lambda: tmp.write_text(text, encoding="utf-8"),
            attempts=retry_attempts,
            delay_seconds=retry_delay_seconds,
        )
        _sync_file(tmp)
        for asset_tmp, asset_path in staged_assets:
            os.replace(asset_tmp, asset_path)
            _sync_file(asset_path)
        if staged_assets:
            _sync_directory(staged_assets[0][1].parent)
        _retry_transient_io(
            lambda: os.replace(tmp, path),
            attempts=retry_attempts,
            delay_seconds=retry_delay_seconds,
        )
        _sync_directory(path.parent)
        _verify_written_text(
            path,
            text,
            rendered_operations,
            retry_attempts,
            retry_delay_seconds,
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        for asset_tmp, _asset_path in staged_assets:
            asset_tmp.unlink(missing_ok=True)
        for asset_path in created_assets:
            asset_path.unlink(missing_ok=True)
        raise

    return WriteOutcome(
        path=path,
        source_id=build_source_id(path, journal_root),
        before_hash=before_hash,
        after_hash=_sha256(text),
        sections=tuple(sections),
        new_file=new_file,
    )


def verify_committed_operations(
    path: Path,
    operations: Sequence[DraftOperation],
    *,
    attachments: Sequence[MediaAttachment] = (),
) -> bool:
    """Re-read a note and verify every committed patch in its target section."""
    rendered = _render_attachments(operations, attachments)
    try:
        text = _retry_transient_io(
            lambda: path.read_text(encoding="utf-8"),
            attempts=3,
            delay_seconds=0.2,
        )
    except OSError:
        return False
    return all(
        section_contains_entry(text, operation.section, operation.content)
        for operation in rendered
    )


def _verify_written_text(
    path: Path,
    expected: str,
    operations: Sequence[DraftOperation],
    attempts: int,
    delay_seconds: float,
) -> None:
    observed = _retry_transient_io(
        lambda: path.read_text(encoding="utf-8"),
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    entries_present = all(
        section_contains_entry(observed, operation.section, operation.content)
        for operation in operations
    )
    if _sha256(observed) != _sha256(expected) or not entries_present:
        raise DraftError(
            DraftErrorCode.WRITE_VERIFICATION_FAILED,
            "journal write did not pass read-back verification",
        )


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
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


def _render_attachments(
    operations: Sequence[DraftOperation], attachments: Sequence[MediaAttachment]
) -> Tuple[DraftOperation, ...]:
    rendered = list(operations)
    if not attachments:
        return tuple(rendered)
    embeds = "\n".join(f"  ![[{item.sha256}{item.extension}]]" for item in attachments)
    last = rendered[-1]
    rendered[-1] = DraftOperation(last.section, f"{last.content}\n{embeds}")
    return tuple(rendered)


def _prepare_assets(
    journal_root: Path, attachments: Sequence[MediaAttachment]
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    assets_dir = journal_root / "assets"
    staged = []
    created = []
    if attachments:
        assets_dir.mkdir(parents=True, exist_ok=True)
    for item in attachments:
        source = Path(item.staged_path)
        if _sha256_bytes(source.read_bytes()) != item.sha256:
            raise OSError("staged image hash mismatch")
        target = assets_dir / f"{item.sha256}{item.extension}"
        if target.exists():
            if _sha256_bytes(target.read_bytes()) != item.sha256:
                raise OSError("existing image hash mismatch")
            continue
        temporary = assets_dir / f".{target.name}.tmp-{uuid.uuid4().hex}"
        shutil.copyfile(source, temporary)
        staged.append((temporary, target))
        created.append(target)
    return staged, created


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
