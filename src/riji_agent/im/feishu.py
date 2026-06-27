"""Feishu IM adapter models."""

from __future__ import annotations

from dataclasses import dataclass

from riji_agent.im.models import IncomingChatMessage

FEISHU_PLATFORM = "feishu"


@dataclass(frozen=True)
class FeishuIncomingMessage:
    """A Feishu message before platform-neutral normalization."""

    event_id: str
    feishu_user_id: str
    chat_id: str
    chat_type: str
    text: str

    def to_chat_message(self) -> IncomingChatMessage:
        return IncomingChatMessage(
            event_id=self.event_id,
            user_id=self.feishu_user_id,
            chat_id=self.chat_id,
            chat_type=self.chat_type,
            text=self.text,
            platform=FEISHU_PLATFORM,
        )
