"""Validate, stage, group, and expire inbound Feishu images locally."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from riji_agent.media.models import MediaAttachment, MediaError, MediaErrorCode
from riji_agent.timezone import local_journal_timezone

MAX_IMAGES_PER_EVENT = 6
MAX_IMAGE_BYTES = 10 * 1024 * 1024
CAPTURE_WINDOW_SECONDS = 120
STAGING_TTL_HOURS = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_attachments (
    attachment_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    staged_path TEXT NOT NULL,
    bound_draft_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, part_index)
);
CREATE TABLE IF NOT EXISTS media_captures (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    attachment_ids TEXT NOT NULL,
    draft_id TEXT,
    last_activity TEXT NOT NULL
);
"""


def _default_now() -> datetime:
    return datetime.now(local_journal_timezone())


class MediaService:
    def __init__(
        self,
        database_path: Path,
        staging_dir: Path,
        *,
        now: Callable[[], datetime] = _default_now,
    ) -> None:
        self._database_path = Path(database_path)
        self._staging_dir = Path(staging_dir)
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._staging_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._now = now

    def close(self) -> None:
        self._conn.close()

    def stage(self, event_id: str, part_index: int, data: bytes) -> MediaAttachment:
        if not event_id or part_index < 0 or part_index >= MAX_IMAGES_PER_EVENT:
            raise MediaError(MediaErrorCode.INVALID_PART, "invalid image part")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise MediaError(MediaErrorCode.TOO_LARGE, "image exceeds the size limit")
        media_type, extension = _sniff_image(data)
        digest = hashlib.sha256(data).hexdigest()
        existing = self._find_part(event_id, part_index)
        if existing is not None:
            if existing.sha256 != digest:
                raise MediaError(MediaErrorCode.CONTENT_MISMATCH, "image part changed")
            return existing

        attachment_id = uuid.uuid4().hex
        path = self._staging_dir / f"{attachment_id}{extension}"
        _write_private(path, data)
        attachment = MediaAttachment(
            attachment_id=attachment_id,
            event_id=event_id,
            part_index=part_index,
            sha256=digest,
            media_type=media_type,
            extension=extension,
            size_bytes=len(data),
            staged_path=str(path),
        )
        self._save_attachment(attachment)
        self.cleanup_expired()
        return attachment

    def resolve(self, event_id: str, attachment_ids: Sequence[str]) -> Tuple[MediaAttachment, ...]:
        resolved = []
        for attachment_id in dict.fromkeys(attachment_ids):
            row = self._conn.execute(
                "SELECT * FROM media_attachments WHERE attachment_id = ? AND event_id = ?",
                (attachment_id, event_id),
            ).fetchone()
            if row is None:
                raise MediaError(MediaErrorCode.ATTACHMENT_NOT_FOUND, "image token is invalid")
            resolved.append(_to_attachment(row))
        return tuple(resolved)

    def active_capture(self, session_id: str) -> Tuple[MediaAttachment, ...]:
        row = self._active_capture_row(session_id)
        if row is None:
            return ()
        return self._get_many(json.loads(row["attachment_ids"]))

    def capture_draft_id(self, session_id: str) -> Optional[str]:
        row = self._active_capture_row(session_id)
        return str(row["draft_id"]) if row is not None and row["draft_id"] else None

    def add_to_capture(
        self,
        session_id: str,
        user_id: str,
        attachments: Sequence[MediaAttachment],
    ) -> Tuple[MediaAttachment, ...]:
        current = list(self.active_capture(session_id))
        known = {item.attachment_id for item in current}
        current.extend(item for item in attachments if item.attachment_id not in known)
        if len(current) > MAX_IMAGES_PER_EVENT:
            raise MediaError(MediaErrorCode.INVALID_PART, "a draft supports at most six images")
        draft_id = self.capture_draft_id(session_id)
        self._conn.execute(
            "INSERT OR REPLACE INTO media_captures "
            "(session_id, user_id, attachment_ids, draft_id, last_activity) VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                user_id,
                json.dumps([item.attachment_id for item in current]),
                draft_id,
                self._now().isoformat(),
            ),
        )
        self._conn.commit()
        return tuple(current)

    def open_capture(self, session_id: str, user_id: str) -> Tuple[MediaAttachment, ...]:
        return self.add_to_capture(session_id, user_id, ())

    def bind_capture(self, session_id: str, draft_id: str) -> None:
        capture = self.active_capture(session_id)
        self._conn.execute(
            "UPDATE media_captures SET draft_id = ?, last_activity = ? WHERE session_id = ?",
            (draft_id, self._now().isoformat(), session_id),
        )
        self._conn.executemany(
            "UPDATE media_attachments SET bound_draft_id = ? WHERE attachment_id = ?",
            ((draft_id, item.attachment_id) for item in capture),
        )
        self._conn.commit()

    def bind_attachments(
        self, draft_id: str, attachments: Sequence[MediaAttachment]
    ) -> None:
        self._conn.executemany(
            "UPDATE media_attachments SET bound_draft_id = ? WHERE attachment_id = ?",
            ((draft_id, item.attachment_id) for item in attachments),
        )
        self._conn.commit()

    def clear_capture(self, session_id: str, *, discard: bool = False) -> None:
        row = self._conn.execute(
            "SELECT attachment_ids FROM media_captures WHERE session_id = ?", (session_id,)
        ).fetchone()
        self._conn.execute("DELETE FROM media_captures WHERE session_id = ?", (session_id,))
        self._conn.commit()
        if discard and row is not None:
            self._delete_attachments(json.loads(row["attachment_ids"]))

    def finish_draft(self, draft_id: str) -> None:
        rows = self._conn.execute(
            "SELECT attachment_id FROM media_attachments WHERE bound_draft_id = ?", (draft_id,)
        ).fetchall()
        ids = [row["attachment_id"] for row in rows]
        self._delete_attachments(ids)
        self._conn.execute("DELETE FROM media_captures WHERE draft_id = ?", (draft_id,))
        self._conn.commit()

    def discard_draft(self, draft_id: str) -> None:
        self.finish_draft(draft_id)

    def cleanup_expired(self) -> int:
        cutoff = self._now() - timedelta(hours=STAGING_TTL_HOURS)
        rows = self._conn.execute(
            "SELECT attachment_id FROM media_attachments WHERE created_at < ?",
            (cutoff.isoformat(),),
        ).fetchall()
        ids = [row["attachment_id"] for row in rows]
        self._delete_attachments(ids)
        self._conn.execute("DELETE FROM media_captures WHERE last_activity < ?", (cutoff.isoformat(),))
        self._conn.commit()
        return len(ids)

    def _active_capture_row(self, session_id: str) -> Optional[sqlite3.Row]:
        row = self._conn.execute(
            "SELECT * FROM media_captures WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        last = datetime.fromisoformat(row["last_activity"])
        if self._now() - last <= timedelta(seconds=CAPTURE_WINDOW_SECONDS):
            return row
        self.clear_capture(session_id, discard=not bool(row["draft_id"]))
        return None

    def _get_many(self, attachment_ids: Sequence[str]) -> Tuple[MediaAttachment, ...]:
        items = []
        for attachment_id in attachment_ids:
            row = self._conn.execute(
                "SELECT * FROM media_attachments WHERE attachment_id = ?", (attachment_id,)
            ).fetchone()
            if row is not None:
                items.append(_to_attachment(row))
        return tuple(items)

    def _find_part(self, event_id: str, part_index: int) -> Optional[MediaAttachment]:
        row = self._conn.execute(
            "SELECT * FROM media_attachments WHERE event_id = ? AND part_index = ?",
            (event_id, part_index),
        ).fetchone()
        return _to_attachment(row) if row is not None else None

    def _save_attachment(self, item: MediaAttachment) -> None:
        self._conn.execute(
            "INSERT INTO media_attachments "
            "(attachment_id, event_id, part_index, sha256, media_type, extension, "
            "size_bytes, staged_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.attachment_id,
                item.event_id,
                item.part_index,
                item.sha256,
                item.media_type,
                item.extension,
                item.size_bytes,
                item.staged_path,
                self._now().isoformat(),
            ),
        )
        self._conn.commit()

    def _delete_attachments(self, attachment_ids: Sequence[str]) -> None:
        for item in self._get_many(attachment_ids):
            try:
                Path(item.staged_path).unlink(missing_ok=True)
            except OSError:
                pass
        self._conn.executemany(
            "DELETE FROM media_attachments WHERE attachment_id = ?",
            ((attachment_id,) for attachment_id in attachment_ids),
        )
        self._conn.commit()


def _to_attachment(row: sqlite3.Row) -> MediaAttachment:
    return MediaAttachment(
        attachment_id=row["attachment_id"],
        event_id=row["event_id"],
        part_index=row["part_index"],
        sha256=row["sha256"],
        media_type=row["media_type"],
        extension=row["extension"],
        size_bytes=row["size_bytes"],
        staged_path=row["staged_path"],
    )


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def _sniff_image(data: bytes) -> tuple[str, str]:
    signatures = (
        (data.startswith(b"\x89PNG\r\n\x1a\n"), "image/png", ".png"),
        (data.startswith(b"\xff\xd8\xff"), "image/jpeg", ".jpg"),
        (data.startswith((b"GIF87a", b"GIF89a")), "image/gif", ".gif"),
        (data.startswith(b"BM"), "image/bmp", ".bmp"),
        (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP", "image/webp", ".webp"),
    )
    for matched, media_type, extension in signatures:
        if matched:
            return media_type, extension
    raise MediaError(MediaErrorCode.UNSUPPORTED_TYPE, "unsupported image content")
