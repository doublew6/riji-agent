"""Multi-turn tool-calling loop: model plans -> local tools -> model continues.

The model never receives the vault, source paths or credentials. It can only
call the registered retrieval tools, a bounded number of times, and the final
answer is expected to separate journal facts, model inference and gaps, with a
source list.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from riji_agent.agent.tools import ToolRegistry, openai_tool_specs
from riji_agent.agent.evidence import JOURNAL_EVIDENCE_BOUNDARIES
from riji_agent.agent.discussion_recall import requests_discussion_recall
from riji_agent.models.types import AssistantTurn, LLMProvider
from riji_agent.observability import runtime_span, runtime_trace
from riji_agent.retrieval.models import ToolContext

SYSTEM_PROMPT = (
    "你是日记 Agent，只能通过提供的工具检索用户的本地日记。"
    "你看不到完整 vault、文件路径或任何凭据。"
    "请按需多轮调用工具来规划检索；得到证据后再回答。"
    "最终回答必须清楚区分三部分：(1) 日记事实（附来源 [[riji/...]]）、"
    "(2) 你的推断（标明是推断，不是日记原文）、(3) 证据不足之处。"
    "不要编造日记中不存在的内容；证据不足时直接说明。"
    "工具结果中 content_type 为 ai_discussion_result 的内容仅是 AI 讨论资料，"
    "必须保留其来源和当时条件；保存确认不等于采纳，建议和计划不等于已发生事实。"
)


@dataclass(frozen=True)
class AgentLimits:
    max_rounds: int = 6
    max_tool_calls: int = 12
    # Prior session turns replayed for continuity, bounded by count and total
    # characters (oldest trimmed first) to honour egress minimisation.
    max_history_messages: int = 12
    max_history_chars: int = 4000


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
    trace_id: Optional[str] = None
    opik_trace_id: Optional[str] = None


@dataclass
class _LoopState:
    messages: List[Dict[str, Any]]
    audit: List[AuditEntry] = field(default_factory=list)
    sources: Set[str] = field(default_factory=set)
    tool_calls: int = 0
    egress_guards: List[Callable[[], None]] = field(default_factory=list)


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        *,
        limits: Optional[AgentLimits] = None,
        tool_specs: Optional[Sequence[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        runtime_trace_policy_path: Optional[Path] = None,
        before_send: Optional[Callable[[], None]] = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._limits = limits or AgentLimits()
        self._tool_specs = list(tool_specs) if tool_specs is not None else openai_tool_specs()
        self._system_prompt = (system_prompt or SYSTEM_PROMPT) + "\n" + JOURNAL_EVIDENCE_BOUNDARIES
        self._runtime_trace_policy_path = runtime_trace_policy_path
        self._before_send = before_send

    def run(
        self,
        context: ToolContext,
        question: str,
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> AgentResult:
        advertised = {spec["function"]["name"] for spec in self._tool_specs}
        if context.allowed_tools is not None:
            advertised.intersection_update(context.allowed_tools)
        context = replace(context, allowed_tools=tuple(sorted(advertised)),
                          include_ai_discussions=(context.include_ai_discussions or (
                              context.chat_type == "p2p" and context.purpose == "private_chat"
                              and requests_discussion_recall(question))))
        with runtime_trace(
            self._runtime_trace_policy_path,
            name="agent.run",
            prompt={"question": question},
            metadata={"request_id": context.request_id, "persona_id": context.persona_id},
        ) as trace:
            request_scope = getattr(self._provider, "request_scope", nullcontext)
            with request_scope():
                result = self._run_loop(context, question, history=history)
            trace.set_output(result.answer)
        return replace(
            result,
            trace_id=trace.trace_id,
            opik_trace_id=trace.external_trace_id,
        )

    def _run_loop(
        self,
        context: ToolContext,
        question: str,
        *,
        history: Sequence[Mapping[str, str]],
    ) -> AgentResult:
        messages: List[Dict[str, Any]] = [{"role": "system", "content": self._system_prompt}]
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": question})
        state = _LoopState(messages)

        for round_index in range(self._limits.max_rounds):
            with runtime_span(
                "agent.round",
                input_value={"round": round_index + 1},
                metadata={"round": round_index + 1},
            ) as round_span:
                turn = self._complete(state, self._tool_specs, round_index + 1)
                round_span.set_output(
                    {"tool_calls": len(turn.tool_calls), "has_answer": bool(turn.content)}
                )
                if not turn.tool_calls:
                    return self._result(
                        context,
                        turn.content or "",
                        state,
                        round_index + 1,
                        False,
                    )

                state.messages.append(self._assistant_message(turn))
                self._invoke_tools(context, turn, state)

        # Rounds exhausted: ask once more without tools to force a final answer.
        state.messages.append(
            {"role": "system", "content": "请基于已收集的证据立即给出最终回答，不要再调用工具。"}
        )
        with runtime_span(
            "agent.finalize",
            input_value={"round": self._limits.max_rounds + 1},
            metadata={"forced": True},
        ):
            final = self._complete(state, [], self._limits.max_rounds + 1)
        return self._result(
            context,
            final.content or "",
            state,
            self._limits.max_rounds,
            True,
        )

    def _complete(
        self,
        state: _LoopState,
        tools: Sequence[Dict[str, Any]],
        round_number: int,
    ) -> AssistantTurn:
        provider = getattr(self._provider, "provider_name", type(self._provider).__name__)
        model = getattr(self._provider, "model_name", None)
        with runtime_span(
            "provider.complete",
            span_type="llm",
            input_value={"messages": self._trace_messages(state.messages), "tools": list(tools)},
            metadata={"round": round_number},
            model=model,
            provider=provider,
        ) as span:
            def check() -> None:
                if self._before_send is not None:
                    self._before_send()
                for guard in state.egress_guards:
                    guard()

            guarded_complete = getattr(self._provider, "complete_with_guard", None)
            if guarded_complete is None:
                check()
                turn = self._provider.complete(state.messages, tools)
            else:
                turn = guarded_complete(state.messages, tools, before_send=check)
            span.set_output(self._trace_messages([self._assistant_message(turn)])[0])
            span.set_outcome(ok=True)
            return turn

    def _invoke_tools(
        self,
        context: ToolContext,
        turn: AssistantTurn,
        state: _LoopState,
    ) -> None:
        for call in turn.tool_calls:
            if state.tool_calls >= self._limits.max_tool_calls:
                payload = {"error": "tool_budget_exceeded"}
                state.messages.append(self._tool_message(call.id, payload))
                state.audit.append(
                    AuditEntry(
                        call.name,
                        False,
                        "tool_budget_exceeded",
                        (),
                        context.request_id,
                    )
                )
                continue
            invocation = self._registry.invoke(context, call.name, call.arguments)
            if invocation.before_send is not None:
                state.egress_guards.append(invocation.before_send)
            state.tool_calls += 1
            state.messages.append(self._tool_message(call.id, invocation.payload))
            state.audit.append(
                AuditEntry(
                    call.name,
                    invocation.ok,
                    invocation.error,
                    invocation.source_ids,
                    context.request_id,
                )
            )
            state.sources.update(invocation.source_ids)

    # ------------------------------------------------------------- helpers

    def _history_messages(self, history: Sequence[Mapping[str, str]]) -> List[Dict[str, str]]:
        """Bounded prior turns to prepend, oldest trimmed first.

        Only user/assistant roles with non-empty content are replayed; the most
        recent ``max_history_messages`` are considered, then a character budget
        drops the oldest until the total fits (always keeping at least one).
        """
        cleaned = [
            {"role": role, "content": content}
            for item in history
            for role in (item.get("role"),)
            for content in ((item.get("content") or "").strip(),)
            if role in ("user", "assistant") and content
        ]
        if not cleaned:
            return []
        cleaned = cleaned[-self._limits.max_history_messages :]

        kept: List[Dict[str, str]] = []
        total = 0
        for message in reversed(cleaned):
            total += len(message["content"])
            if total > self._limits.max_history_chars and kept:
                break
            kept.append(message)
        kept.reverse()
        return kept

    @staticmethod
    def _assistant_message(turn: AssistantTurn) -> Dict[str, Any]:
        message = {
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
        if turn.tool_calls and turn.reasoning_content is not None:
            message["reasoning_content"] = turn.reasoning_content
        return message

    @staticmethod
    def _trace_messages(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Runtime observability never copies private continuation reasoning."""
        return [{key: value for key, value in message.items() if key != "reasoning_content"}
                for message in messages]

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
        state: _LoopState,
        rounds: int,
        exceeded: bool,
    ) -> AgentResult:
        return AgentResult(
            request_id=context.request_id,
            answer=answer,
            sources=tuple(sorted(state.sources)),
            rounds=rounds,
            tool_calls=state.tool_calls,
            exceeded_rounds=exceeded,
            audit=tuple(state.audit),
        )
