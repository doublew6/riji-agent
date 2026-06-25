"""Multi-turn tool-calling loop: model plans -> local tools -> model continues.

The model never receives the vault, source paths or credentials. It can only
call the registered retrieval tools, a bounded number of times, and the final
answer is expected to separate journal facts, model inference and gaps, with a
source list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from riji_agent.agent.tools import ToolRegistry, openai_tool_specs
from riji_agent.llm.types import AssistantTurn, LLMProvider
from riji_agent.retrieval.models import ToolContext

SYSTEM_PROMPT = (
    "你是日记 Agent，只能通过提供的工具检索用户的本地日记。"
    "你看不到完整 vault、文件路径或任何凭据。"
    "请按需多轮调用工具来规划检索；得到证据后再回答。"
    "最终回答必须清楚区分三部分：(1) 日记事实（附来源 [[riji/...]]）、"
    "(2) 你的推断（标明是推断，不是日记原文）、(3) 证据不足之处。"
    "不要编造日记中不存在的内容；证据不足时直接说明。"
)


@dataclass(frozen=True)
class AgentLimits:
    max_rounds: int = 6
    max_tool_calls: int = 12


@dataclass(frozen=True)
class AuditEntry:
    """Per tool-call audit metadata; records source ids, not full content."""

    tool: str
    ok: bool
    error: Optional[str]
    source_ids: Tuple[str, ...]
    request_id: str


@dataclass(frozen=True)
class AgentResult:
    request_id: str
    answer: str
    sources: Tuple[str, ...]
    rounds: int
    tool_calls: int
    exceeded_rounds: bool
    audit: Tuple[AuditEntry, ...] = field(default_factory=tuple)


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        *,
        limits: Optional[AgentLimits] = None,
        tool_specs: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._limits = limits or AgentLimits()
        self._tool_specs = list(tool_specs) if tool_specs is not None else openai_tool_specs()

    def run(self, context: ToolContext, question: str) -> AgentResult:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        audit: List[AuditEntry] = []
        sources: Set[str] = set()
        tool_calls = 0

        for round_index in range(self._limits.max_rounds):
            turn = self._provider.complete(messages, self._tool_specs)
            if not turn.tool_calls:
                return self._result(
                    context, turn.content or "", sources, round_index + 1, tool_calls, False, audit
                )

            messages.append(self._assistant_message(turn))
            for call in turn.tool_calls:
                if tool_calls >= self._limits.max_tool_calls:
                    payload = {"error": "tool_budget_exceeded"}
                    messages.append(self._tool_message(call.id, payload))
                    audit.append(AuditEntry(call.name, False, "tool_budget_exceeded", (), context.request_id))
                    continue
                invocation = self._registry.invoke(context, call.name, call.arguments)
                tool_calls += 1
                messages.append(self._tool_message(call.id, invocation.payload))
                audit.append(
                    AuditEntry(
                        call.name,
                        invocation.ok,
                        invocation.error,
                        invocation.source_ids,
                        context.request_id,
                    )
                )
                sources.update(invocation.source_ids)

        # Rounds exhausted: ask once more without tools to force a final answer.
        messages.append(
            {"role": "system", "content": "请基于已收集的证据立即给出最终回答，不要再调用工具。"}
        )
        final = self._provider.complete(messages, [])
        return self._result(
            context, final.content or "", sources, self._limits.max_rounds, tool_calls, True, audit
        )

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _assistant_message(turn: AssistantTurn) -> Dict[str, Any]:
        return {
            "role": "assistant",
            "content": turn.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in turn.tool_calls
            ],
        }

    @staticmethod
    def _tool_message(call_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(payload, ensure_ascii=False),
        }

    @staticmethod
    def _result(
        context: ToolContext,
        answer: str,
        sources: Set[str],
        rounds: int,
        tool_calls: int,
        exceeded: bool,
        audit: List[AuditEntry],
    ) -> AgentResult:
        return AgentResult(
            request_id=context.request_id,
            answer=answer,
            sources=tuple(sorted(sources)),
            rounds=rounds,
            tool_calls=tool_calls,
            exceeded_rounds=exceeded,
            audit=tuple(audit),
        )
