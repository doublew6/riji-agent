"""Data models for the Hermes -> riji-agent gateway boundary."""

from __future__ import annotations

from dataclasses import dataclass

from riji_agent.im.feishu import FeishuIncomingMessage
from riji_agent.im.models import PRIVATE_CHAT_TYPE


@dataclass(frozen=True)
class IncomingMessage(FeishuIncomingMessage):
    """A single Feishu message forwarded by Hermes."""


@dataclass(frozen=True)
class GatewayReply:
    request_id: str
    persona_id: str
    text: str
    deduplicated: bool = False
