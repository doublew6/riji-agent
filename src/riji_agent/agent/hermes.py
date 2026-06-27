"""Hermes default agent runtime adapter."""

from riji_agent.hermes.api import build_hermes_router as build_hermes_runtime_router
from riji_agent.hermes.gateway import HermesGateway as HermesAgentRuntime

__all__ = ["HermesAgentRuntime", "build_hermes_runtime_router"]
