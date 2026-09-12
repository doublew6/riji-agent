"""Private runtime tracing adapter for real Agent executions.

This module records only in-memory ``evalmesh.runtime-trace.v1`` envelopes.
EvalMesh owns policy validation, bounded recursive redaction, private JSONL
persistence, and delivery to the explicitly configured private Opik project.
When no policy path is configured, every helper is a no-op.
"""

from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Tuple
from uuid import uuid4

_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOG = logging.getLogger("riji_agent.runtime_trace")
_CURRENT_TRACE: ContextVar[Optional["RuntimeTrace"]] = ContextVar(
    "riji_agent_runtime_trace", default=None
)
_Submitter = Callable[[str | Path, Any], Any]
_Validator = Callable[[str | Path], Any]


class RuntimeTracingUnavailable(RuntimeError):
    """Tracing was enabled but the compatible private EvalMesh runtime is absent."""


def _load_evalmesh() -> Tuple[_Validator, _Submitter]:
    try:
        from evalmesh.runtime_tracing import (
            load_runtime_trace_config,
            submit_runtime_trace,
        )
    except ImportError:
        raise RuntimeTracingUnavailable(
            "runtime tracing requires a compatible EvalMesh installation"
        ) from None
    return load_runtime_trace_config, submit_runtime_trace


def _opaque_id(value: str, fallback: str) -> str:
    if _PUBLIC_ID.fullmatch(value):
        return value
    cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "-", value).strip("-._:")
    if not cleaned:
        return fallback
    return cleaned[:128]


class RuntimeSpan:
    def __init__(
        self,
        trace: Optional["RuntimeTrace"],
        *,
        name: str,
        span_type: Literal["general", "tool", "llm"],
        input_value: Any,
        metadata: Optional[dict[str, Any]],
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> None:
        self._trace = trace
        self._record: dict[str, Any] = {
            "id": uuid4().hex,
            "parent_id": None,
            "name": _opaque_id(name, "runtime.span"),
            "type": span_type,
            "started_at": "",
            "input": input_value,
            "metadata": metadata or {},
        }
        if model:
            self._record["model"] = _opaque_id(model, "model")
        if provider:
            self._record["provider"] = _opaque_id(provider, "provider")
        self._stack_token: Optional[Token[tuple[dict[str, Any], ...]]] = None
        self._started = 0.0
        self._outcome_ok: Optional[bool] = None

    def __enter__(self) -> "RuntimeSpan":
        if self._trace is None:
            return self
        if self._stack_token is not None:
            raise RuntimeError("runtime trace span cannot be entered twice")
        stack = self._trace._span_stack.get()
        self._record["parent_id"] = stack[-1]["id"] if stack else None
        self._record["started_at"] = datetime.now(UTC).isoformat()
        self._trace._spans.append(self._record)
        self._stack_token = self._trace._span_stack.set((*stack, self._record))
        self._started = time.perf_counter()
        return self

    def set_output(self, value: Any) -> None:
        if self._trace is not None:
            self._record["output"] = value

    def set_outcome(self, *, ok: bool, error: Optional[str] = None) -> None:
        if self._trace is None:
            return
        metadata = self._record["metadata"]
        self._outcome_ok = ok
        metadata["outcome"] = "success" if ok else "failure"
        if error:
            metadata["error_code"] = _opaque_id(error, "error")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self._trace is None:
            return False
        stack = self._trace._span_stack.get()
        if self._stack_token is None or not stack or stack[-1] is not self._record:
            raise RuntimeError("runtime trace spans must close in stack order")
        metadata = self._record["metadata"]
        metadata["duration_ms"] = round((time.perf_counter() - self._started) * 1000, 3)
        metadata["status"] = (
            "error" if exc_type is not None or self._outcome_ok is False else "ok"
        )
        self._record["completed_at"] = datetime.now(UTC).isoformat()
        self._trace._span_stack.reset(self._stack_token)
        self._stack_token = None
        return False


class RuntimeTrace:
    def __init__(
        self,
        policy_path: Optional[Path],
        *,
        name: str,
        prompt: Any,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self._policy_path = policy_path
        self._name = _opaque_id(name, "agent.run")
        self._prompt = prompt
        self._metadata = metadata or {}
        self._trace_id = uuid4().hex
        self._started_at = ""
        self._output: Any = None
        self._output_set = False
        self._spans: list[dict[str, Any]] = []
        self._span_stack: ContextVar[tuple[dict[str, Any], ...]] = ContextVar(
            f"riji_agent_span_stack_{self._trace_id}", default=()
        )
        self._active_token: Optional[Token[Optional[RuntimeTrace]]] = None
        self.receipt: Any = None

    @property
    def trace_id(self) -> Optional[str]:
        return self._trace_id if self._policy_path is not None else None

    @property
    def external_trace_id(self) -> Optional[str]:
        value = getattr(self.receipt, "external_id", None)
        return value if isinstance(value, str) else None

    def __enter__(self) -> "RuntimeTrace":
        if self._policy_path is None:
            return self
        validator, _submitter = _load_evalmesh()
        validator(self._policy_path)
        self._started_at = datetime.now(UTC).isoformat()
        self._active_token = _CURRENT_TRACE.set(self)
        return self

    def set_output(self, value: Any) -> None:
        if self._policy_path is not None:
            self._output = value
            self._output_set = True

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self._policy_path is None:
            return False
        if self._active_token is None or self._span_stack.get():
            raise RuntimeError("runtime trace did not close correctly")
        try:
            event: dict[str, Any] = {
                "protocol": "evalmesh.runtime-trace.v1",
                "trace_id": self._trace_id,
                "name": self._name,
                "started_at": self._started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "prompt": self._prompt,
                "metadata": {
                    **self._metadata,
                    "status": "error" if exc_type is not None else "ok",
                },
                "tags": ["riji-agent"],
                "spans": self._spans,
            }
            if self._output_set:
                event["output"] = self._output
            _validator, submitter = _load_evalmesh()
            self.receipt = submitter(self._policy_path, event)
            _LOG.info(
                "runtime trace submitted trace_id=%s opik_trace_id=%s "
                "stored=%s delivered=%s error_code=%s",
                self._trace_id,
                self.external_trace_id,
                getattr(self.receipt, "stored", None),
                getattr(self.receipt, "delivered", None),
                getattr(self.receipt, "error_code", None),
            )
        finally:
            _CURRENT_TRACE.reset(self._active_token)
            self._active_token = None
        return False


def runtime_trace(
    policy_path: Optional[Path],
    *,
    name: str,
    prompt: Any,
    metadata: Optional[dict[str, Any]] = None,
) -> RuntimeTrace:
    return RuntimeTrace(policy_path, name=name, prompt=prompt, metadata=metadata)


def runtime_span(
    name: str,
    *,
    span_type: Literal["general", "tool", "llm"] = "general",
    input_value: Any = None,
    metadata: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> RuntimeSpan:
    return RuntimeSpan(
        _CURRENT_TRACE.get(),
        name=name,
        span_type=span_type,
        input_value=input_value,
        metadata=metadata,
        model=model,
        provider=provider,
    )
