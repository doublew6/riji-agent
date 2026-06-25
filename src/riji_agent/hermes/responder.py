"""Production Responder: run the DeepSeek tool-calling loop per persona.

The persona's assembled system prompt (including shared memory) is injected into
the loop. Per-turn chat history wiring into the loop is a later refinement; the
gateway already persists it.
"""

from __future__ import annotations

from typing import Optional, Sequence

from riji_agent.agent.loop import AgentLimits, AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.audit.store import AuditStore
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
        audit_store: Optional[AuditStore] = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._limits = limits
        self._audit = audit_store

    def respond(
        self,
        context: ToolContext,
        system_prompt: str,
        history: Sequence[SessionMessage],
        question: str,
        allowed_tools: Sequence[str] = (),
    ) -> str:
        runner = AgentRunner(
            self._provider,
            self._tools,
            limits=self._limits,
            tool_specs=self._tools.tool_specs(allowed_tools or None),
            system_prompt=system_prompt,
        )
        result = runner.run(context, question)
        if self._audit is not None:
            for entry in result.audit:
                self._audit.record(
                    request_id=context.request_id,
                    persona_id=context.persona_id,
                    feishu_user_id=context.feishu_user_id,
                    tool=entry.tool,
                    ok=entry.ok,
                    error=entry.error,
                    source_ids=entry.source_ids,
                )
        return result.answer
