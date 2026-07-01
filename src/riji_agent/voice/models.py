"""Voice reply data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceAttachment:
    """A local audio file ready for the IM adapter to deliver."""

    path: str
    mime_type: str
