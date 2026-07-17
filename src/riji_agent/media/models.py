"""Models and stable errors for locally staged message media."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MediaErrorCode(str, Enum):
    INVALID_PART = "invalid_part"
    TOO_LARGE = "too_large"
    UNSUPPORTED_TYPE = "unsupported_type"
    CONTENT_MISMATCH = "content_mismatch"
    ATTACHMENT_NOT_FOUND = "attachment_not_found"


class MediaError(Exception):
    def __init__(self, code: MediaErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MediaAttachment:
    attachment_id: str
    event_id: str
    part_index: int
    sha256: str
    media_type: str
    extension: str
    size_bytes: int
    staged_path: str
