"""Data models for the Hermes -> riji-agent gateway boundary."""

from __future__ import annotations

from dataclasses import dataclass

# Feishu private (peer-to-peer) chat type; anything else is treated as a group.
PRIVATE_CHAT_TYPE = "p2p"


@dataclass(frozen=True)
class IncomingMessage:
    """A single Feishu message forwarded by Hermes."""

    event_id: str
    feishu_user_id: str
    chat_id: str
    chat_type: str
    text: str


@dataclass(frozen=True)
class GatewayReply:
    request_id: str
    persona_id: str
    text: str
    deduplicated: bool = False
