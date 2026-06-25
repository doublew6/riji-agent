"""Production Responder: run the DeepSeek tool-calling loop per persona.

The persona's assembled system prompt (including shared memory) is injected into
the loop. Per-turn chat history wiring into the loop is a later refinement; the
gateway already persists it.
"""

from __future__ import annotations

from typing import Optional, Sequence

from riji_agent.agent.loop import AgentLimits, AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.llm.types import LLMProvider
from riji_agent.memory.models import SessionMessage
from riji_agent.retrieval.models import ToolContext


class AgentResponder:
    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        *,
        limits: Optional[AgentLimits] = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._limits = limits

    def respond(
        self,
        context: ToolContext,
        system_prompt: str,
        history: Sequence[SessionMessage],
        question: str,
    ) -> str:
        runner = AgentRunner(
            self._provider, self._tools, limits=self._limits, system_prompt=system_prompt
        )
        return runner.run(context, question).answer
