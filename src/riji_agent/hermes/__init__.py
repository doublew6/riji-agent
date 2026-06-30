"""Hermes gateway: Feishu private-chat entry point with persona routing."""

from riji_agent.hermes.access import authorize_chat, verify_shared_secret
from riji_agent.hermes.api import build_hermes_router
from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.events import EventLog, ProcessedEvent
from riji_agent.hermes.gateway import HermesGateway, Responder
from riji_agent.hermes.models import GatewayReply, IncomingMessage, PRIVATE_CHAT_TYPE
from riji_agent.hermes.responder import AgentResponder
from riji_agent.hermes.routing import PersonaRoute, route_persona

__all__ = [
    "authorize_chat",
    "verify_shared_secret",
    "AuthError",
    "AuthErrorCode",
    "EventLog",
    "ProcessedEvent",
    "HermesGateway",
    "Responder",
    "AgentResponder",
    "GatewayReply",
    "IncomingMessage",
    "PRIVATE_CHAT_TYPE",
    "PersonaRoute",
    "route_persona",
    "build_hermes_router",
]
