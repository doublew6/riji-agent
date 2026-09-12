"""Bounded SSE assembly; incomplete responses never become executable turns."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

from riji_agent.models.types import LLMError

MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_TEXT_CHARS = 512 * 1024
MAX_REASONING_CHARS = 512 * 1024
MAX_TOOL_CALLS = 32
MAX_STREAM_SECONDS = 600.0


def _invalid() -> None:
    raise LLMError("model_output_invalid")


def _lines(chunks: Iterable[bytes], started_at: float) -> Iterator[str]:
    """Decode complete UTF-8 lines, including CR/LF split across chunks."""
    pending = bytearray()
    total, after_cr, first = 0, False, True
    for chunk in chunks:
        if time.monotonic() - started_at >= MAX_STREAM_SECONDS:
            raise LLMError("model_timeout")
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            _invalid()
        for byte in chunk:
            if after_cr and byte == 10:
                after_cr = False
                continue
            after_cr = byte == 13
            if byte in (10, 13):
                text = pending.decode("utf-8")
                yield text.removeprefix("\ufeff") if first else text
                first = False
                pending.clear()
            else:
                pending.append(byte)
                if len(pending) > MAX_LINE_BYTES:
                    _invalid()
    # SSE requires a blank line to dispatch an event; EOF is not a terminator.
    if pending:
        _invalid()


def _events(chunks: Iterable[bytes], started_at: float) -> Iterator[str]:
    data: List[str] = []
    for line in _lines(chunks, started_at):
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
            continue
        name, separator, value = line.partition(":")
        if name == "data":
            data.append(value.removeprefix(" ") if separator else "")
        # Comments and event/id/retry fields have no completion semantics.
    if data:
        _invalid()


@dataclass
class _Text:
    parts: List[str] = field(default_factory=list)
    length: int = 0

    def append(self, value: Any, limit: int = MAX_TEXT_CHARS) -> None:
        if not isinstance(value, str):
            _invalid()
        self.length += len(value)
        if self.length > limit:
            _invalid()
        self.parts.append(value)

    def value(self) -> str:
        return "".join(self.parts)


@dataclass
class _Completion:
    text: Dict[str, _Text] = field(default_factory=dict)
    calls: Dict[int, Dict[str, _Text]] = field(default_factory=dict)
    finish: Optional[str] = None
    completion_id: Optional[str] = None

    def add(self, data: Any) -> None:
        if not isinstance(data, dict) or "error" in data or not isinstance(data.get("choices"), list):
            _invalid()
        if "id" in data:
            value = data["id"]
            if not isinstance(value, str) or not 1 <= len(value) <= 512:
                _invalid()
            if self.completion_id is not None and value != self.completion_id:
                _invalid()
            self.completion_id = value
        choices = data["choices"]
        if not choices and isinstance(data.get("usage"), dict):
            return
        if len(choices) != 1 or not isinstance(choices[0], dict):
            _invalid()
        choice = choices[0]
        if type(choice.get("index")) is not int or choice["index"] != 0 or self.finish:
            _invalid()
        self._delta(choice.get("delta"))
        finish = choice.get("finish_reason")
        if finish == "content_filter":
            raise LLMError("model_refused")
        if finish is not None:
            if finish not in ("stop", "tool_calls"):
                _invalid()
            self.finish = finish

    def _delta(self, delta: Any) -> None:
        if not isinstance(delta, dict):
            _invalid()
        if delta.get("role") not in (None, "assistant"):
            _invalid()
        for key in ("content", "reasoning_content", "refusal"):
            if delta.get(key) is not None:
                limit = MAX_REASONING_CHARS if key == "reasoning_content" else MAX_TEXT_CHARS
                self.text.setdefault(key, _Text()).append(delta[key], limit)
        refusal = self.text.get("refusal")
        if refusal and refusal.value().strip():
            raise LLMError("model_refused")
        calls = delta.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list):
                _invalid()
            for call in calls:
                self._call(call)

    def _call(self, call: Any) -> None:
        if not isinstance(call, dict) or type(call.get("index")) is not int:
            _invalid()
        index = call["index"]
        if not 0 <= index < MAX_TOOL_CALLS or call.get("type") not in (None, "function"):
            _invalid()
        values = self.calls.setdefault(index, {})
        if call.get("id") is not None:
            values.setdefault("id", _Text()).append(call["id"], 1024)
        function = call.get("function", {})
        if not isinstance(function, dict):
            _invalid()
        for key in ("name", "arguments"):
            if function.get(key) is not None:
                values.setdefault(key, _Text()).append(function[key], 128 if key == "name" else MAX_TEXT_CHARS)

    def result(self) -> Dict[str, Any]:
        if self.finish is None or (self.finish == "tool_calls") != bool(self.calls):
            _invalid()
        if sorted(self.calls) != list(range(len(self.calls))):
            _invalid()
        calls = [self._result_call(self.calls[i]) for i in sorted(self.calls)]
        if len({call["id"] for call in calls}) != len(calls):
            _invalid()
        message: Dict[str, Any] = {key: text.value() for key, text in self.text.items()}
        if calls:
            message["tool_calls"] = calls
        return {"choices": [{"message": message, "finish_reason": self.finish}]}

    @staticmethod
    def _result_call(values: Dict[str, _Text]) -> Dict[str, Any]:
        if set(values) != {"id", "name", "arguments"}:
            _invalid()
        return {"id": values["id"].value(), "type": "function", "function": {
            "name": values["name"].value(), "arguments": values["arguments"].value()}}


def read_completion_stream(chunks: Iterable[bytes], *, started_at: Optional[float] = None) -> Dict[str, Any]:
    """Return only after both a successful finish reason and [DONE]."""
    completion = _Completion()
    try:
        for event in _events(chunks, time.monotonic() if started_at is None else started_at):
            if event == "[DONE]":
                return completion.result()
            completion.add(json.loads(event, object_pairs_hook=_unique_object, parse_constant=_invalid_constant))
    except (ValueError, RecursionError):
        raise LLMError("model_output_invalid") from None
    raise LLMError("model_output_invalid")


def _unique_object(pairs: List[Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    _invalid()
