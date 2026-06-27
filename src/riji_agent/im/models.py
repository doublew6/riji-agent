"""Platform-neutral chat message contracts."""

from __future__ import annotations

from dataclasses import dataclass

PRIVATE_CHAT_TYPE = "p2p"


@dataclass(frozen=True)
class IncomingChatMessage:
    """A chat message normalized from any supported IM adapter."""

    event_id: str
    user_id: str
    chat_id: str
    chat_type: str
    text: str
    platform: str
