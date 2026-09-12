"""Explicit model construction and request accounting for synthetic evaluations."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import httpx

from evalmesh_support.environment import ClockSample

from riji_agent.models.types import LLMError, LLMProvider
from riji_agent.models.errors import MODEL_ERROR_CATEGORIES, model_failure_code


def provider_route_policy(name: str) -> dict[str, Any]:
    """Describe fixed evaluation transport settings without inspecting secrets."""
    if name == "deepseek":
        return {"provider": name, "route": "direct", "trust_env": False,
                "tls_verification": True, "environment_ca": "ignored",
                "automatic_route_fallback": False}
    if name == "codex":
        return {"provider": name, "route": "runtime_managed"}
    if name == "controlled":
        return {"provider": name, "route": "no_model"}
    raise ValueError("evaluation_provider_invalid")


class CountedProvider:
    """Preserve optional provider methods; count attempts, not billed requests."""

    def __init__(self, provider: LLMProvider, max_calls: int = 32) -> None:
        self.provider = provider
        self.max_calls = max_calls
        self.calls = 0
        self.request_chars = 0
        self.failures: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = []
        self.trace_bytes = 0

    def _charge(self, values: Any) -> None:
        if self.calls >= self.max_calls:
            raise LLMError("evaluation_call_limit")
        self.calls += 1
        self.request_chars += len(json.dumps(values, ensure_ascii=False, default=str))

    def complete(self, messages: Any, tools: Any) -> Any:
        self._charge([messages, tools])
        return self._invoke(self.provider.complete, messages, tools)

    def _invoke(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        clock = ClockSample.start()
        messages = args[0] if args else kwargs.get("messages", [])
        second = args[1] if len(args) > 1 else kwargs.get("tools", kwargs.get("schema", {}))
        record = {"messages": self._trace_messages(messages), "tools_or_schema": deepcopy(second)}
        try:
            result = operation(*args, **kwargs)
            record["response"] = deepcopy({"content": result.content, "tool_calls": [
                {"name": call.name, "arguments": call.arguments} for call in result.tool_calls]})
            reasoning = getattr(result, "reasoning_content", None)
            if reasoning is not None:
                record["response"]["reasoning_content_chars"] = len(reasoning)
            return result
        except Exception as error:
            safe = failure_observation(error)["output"]
            self.failures.append(safe)
            record["failure"] = safe
            raise
        finally:
            record["timing"] = clock.finish()
            elapsed = record["timing"]["monotonic_elapsed_seconds"]
            record["duration_ms"] = round(elapsed * 1000) if elapsed is not None else 0
            size = len(json.dumps(record, ensure_ascii=False).encode())
            if self.trace_bytes + size <= 4 * 1024 * 1024:
                self.trace.append(record)
                self.trace_bytes += size
            else:
                self.trace.append({"content_omitted": True, "duration_ms": record["duration_ms"],
                                   "timing": record["timing"]})

    @staticmethod
    def _trace_messages(messages: Any) -> Any:
        copied = deepcopy(messages)
        for message in copied:
            if isinstance(message, dict) and "reasoning_content" in message:
                reasoning = message.pop("reasoning_content")
                message["reasoning_content_chars"] = len(reasoning) if isinstance(reasoning, str) else None
        return copied

    def __getattr__(self, name: str) -> Any:
        original = getattr(self.provider, name)
        if name not in {"complete_with_guard", "complete_json_with_guard"}:
            return original

        def counted(*args: Any, **kwargs: Any) -> Any:
            messages = args[0] if args else kwargs.get("messages", [])
            second = args[1] if len(args) > 1 else kwargs.get("tools", kwargs.get("schema", {}))
            self._charge([messages, second])
            return self._invoke(original, *args, **kwargs)

        return counted


def build_provider(name: str, model: str, timeout: float) -> CountedProvider:
    if name == "deepseek":
        from riji_agent.models.deepseek import DeepSeekProvider

        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise ValueError("evaluation_credentials_missing")
        policy = provider_route_policy(name)
        provider = DeepSeekProvider(
            api_key=key, base_url="https://api.deepseek.com", model=model,
            client=httpx.Client(timeout=timeout, trust_env=policy["trust_env"],
                                verify=policy["tls_verification"]),
        )
    elif name == "codex":
        from riji_agent.models.codex import CodexProvider

        home = os.environ.get("RIJI_EVAL_CODEX_HOME", "")
        if not home:
            raise ValueError("evaluation_codex_home_missing")
        provider = CodexProvider(
            binary=os.environ.get("RIJI_EVAL_CODEX_BIN", "codex"),
            model=model, timeout_seconds=timeout, home=Path(home),
        )
    else:
        raise ValueError("evaluation_provider_invalid")
    return CountedProvider(provider)


def failure_observation(error: Exception) -> dict[str, Any]:
    category, code = "adapter", "evaluation_target_failed"
    value = str(error) if isinstance(error, LLMError) else getattr(error, "code", "")
    journal_codes = {
        "journal_invalid_model_output", "journal_invalid_model_json", "journal_incomplete_extraction",
        "journal_invalid_candidates", "journal_ai_content_not_personal_evidence",
        "journal_invalid_candidate", "journal_inference_must_be_observation",
        "journal_evidence_required", "journal_invalid_evidence_quote", "journal_invalid_fact_date",
        "journal_unsupported_fact_date", "journal_invalid_decisions", "journal_incomplete_decisions",
        "journal_invalid_decision", "journal_invalid_relation_target",
    }
    if isinstance(value, str) and value in journal_codes:
        category, code = "application_output", value
    if isinstance(error, LLMError):
        code = model_failure_code(error)
        category = MODEL_ERROR_CATEGORIES[code]
        if value == "evaluation_call_limit":
            category, code = "evaluation_budget", "evaluation_call_limit"
    return {"output": {"observed": {"status": "failed"},
                       "error_category": category, "error_code": code}, "metrics": {}}
