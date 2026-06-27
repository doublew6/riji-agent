"""Provider-neutral agent runtime contract."""

from __future__ import annotations

from typing import Protocol

from riji_agent.hermes.models import GatewayReply
from riji_agent.im.models import IncomingChatMessage


class AgentRuntime(Protocol):
    """Minimal boundary a chat transport calls to obtain an agent reply."""

    def handle(self, shared_secret: str, message: IncomingChatMessage) -> GatewayReply:
        ...
