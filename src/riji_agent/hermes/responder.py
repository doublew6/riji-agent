"""Production Responder: run the DeepSeek tool-calling loop per persona.

The persona's assembled system prompt (including shared memory) is injected into
the loop, along with this persona-private session's bounded prior turns so the
mentor can follow up on earlier context.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from typing import Callable, Optional, Sequence

from riji_agent.agent.loop import AgentLimits, AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.audit.store import AuditStore
from riji_agent.models.types import LLMProvider
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
        runtime_trace_policy_path: Optional[Path] = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._limits = limits
        self._audit = audit_store
        self._runtime_trace_policy_path = runtime_trace_policy_path

    def has_ai_discussion_evidence(self, request_id: str) -> bool:
        return self._tools.has_ai_discussion_evidence(request_id)

    def respond(
        self,
        context: ToolContext,
        system_prompt: str,
        history: Sequence[SessionMessage],
        question: str,
        allowed_tools: Sequence[str] = (),
    ) -> str:
        return self.respond_guarded(context, system_prompt, history, question, allowed_tools)

    def respond_guarded(
        self,
        context: ToolContext,
        system_prompt: str,
        history: Sequence[SessionMessage],
        question: str,
        allowed_tools: Sequence[str] = (),
        *,
        before_send: Optional[Callable[[], None]] = None,
    ) -> str:
        context = replace(context, ai_discussion_history=(context.ai_discussion_history or any(
            message.content_type != "conversation" for message in history)))
        runner = AgentRunner(
            self._provider,
            self._tools,
            limits=self._limits,
            tool_specs=self._tools.tool_specs(allowed_tools or None),
            system_prompt=system_prompt,
            runtime_trace_policy_path=self._runtime_trace_policy_path,
            before_send=before_send,
        )
        result = runner.run(
            context,
            question,
            history=[{"role": m.role, "content": m.content if m.content_type == "conversation" else
                      "[历史 AI 讨论资料：正文不自动重放；再次参考需要显式检索并核验当前来源，不能当作本人经历。]"}
                     for m in history],
        )
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
