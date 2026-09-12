"""Generic OpenAI-compatible chat completion provider.

This is the reusable wire implementation shared by every OpenAI-style endpoint
(DeepSeek is one preset of it). The API key stays inside this object; it is only
ever sent in the Authorization header and never logged or included in raised
errors. Provider identity is separate metadata; failures expose only fixed codes.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Sequence

import httpx

from riji_agent.models.types import AssistantTurn, LLMError, ToolCall
from riji_agent.models.errors import http_failure_code
from riji_agent.models.streaming import MAX_REASONING_CHARS, read_completion_stream


class OpenAICompatibleProvider:
    _stream = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        client: Optional[httpx.Client] = None,
        provider_label: str = "openai",
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._label = provider_label
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def provider_name(self) -> str:
        return self._label

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def response_mode(self) -> str:
        return "sse" if self._stream else "json"

    def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Sequence[Dict[str, Any]],
    ) -> AssistantTurn:
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": self._stream,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"

        try:
            data = self._request(payload)
        except httpx.HTTPStatusError as exc:
            raise LLMError(http_failure_code(exc.response.status_code)) from None
        except httpx.TimeoutException:
            raise LLMError("model_timeout") from None
        except httpx.ConnectError:
            raise LLMError("model_connection_failed") from None
        except httpx.TransportError:
            raise LLMError("model_transport_failed") from None
        except httpx.HTTPError:
            raise LLMError("model_request_failed") from None
        except ValueError:
            raise LLMError("model_output_invalid") from None

        return self._parse(data, self._label)

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._stream:
            started_at = time.monotonic()
            with self._client.stream("POST", self._url, json=payload, headers=headers) as response:
                response.raise_for_status()
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type != "text/event-stream":
                    raise LLMError("model_output_invalid")
                return read_completion_stream(response.iter_bytes(), started_at=started_at)
        response = self._client.post(self._url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse(data: Dict[str, Any], label: str) -> AssistantTurn:
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("model_output_invalid") from None
        if not isinstance(message, dict):
            raise LLMError("model_output_invalid")
        refusal = message.get("refusal")
        if refusal is not None and not isinstance(refusal, str):
            raise LLMError("model_output_invalid")
        if choice.get("finish_reason") == "content_filter" or (refusal and refusal.strip()):
            raise LLMError("model_refused")
        content, raw_calls = message.get("content"), message.get("tool_calls")
        reasoning = message.get("reasoning_content")
        if reasoning is not None and (not isinstance(reasoning, str) or len(reasoning) > MAX_REASONING_CHARS):
            raise LLMError("model_output_invalid")
        if content is not None and not isinstance(content, str):
            raise LLMError("model_output_invalid")
        if raw_calls is not None and not isinstance(raw_calls, list):
            raise LLMError("model_output_invalid")
        tool_calls = tuple(OpenAICompatibleProvider._parse_tool_call(call) for call in (raw_calls or []))
        if content is None and not tool_calls:
            raise LLMError("model_output_invalid")
        return AssistantTurn(content=content, tool_calls=tool_calls, reasoning_content=reasoning)

    @staticmethod
    def _parse_tool_call(call: Any) -> ToolCall:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise LLMError("model_output_invalid")
        function = call["function"]
        values = (call.get("id"), function.get("name"), function.get("arguments"))
        if any(not isinstance(value, str) for value in values) or not all(values[:2]):
            raise LLMError("model_output_invalid")
        return ToolCall(id=values[0], name=values[1], arguments=values[2])
