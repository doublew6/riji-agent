"""DeepSeek OpenAI-compatible chat completion provider.

The API key stays inside this object; it is only ever sent in the Authorization
header and never logged or included in raised errors.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import httpx

from riji_agent.llm.types import AssistantTurn, LLMError, ToolCall


class DeepSeekProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-reasoner",
        timeout: float = 60.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._client = client or httpx.Client(timeout=timeout)

    def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Sequence[Dict[str, Any]],
    ) -> AssistantTurn:
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"

        try:
            response = self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"deepseek request failed with status {exc.response.status_code}") from None
        except httpx.HTTPError:
            raise LLMError("deepseek request failed") from None
        except ValueError:
            raise LLMError("deepseek returned a malformed response") from None

        return self._parse(data)

    @staticmethod
    def _parse(data: Dict[str, Any]) -> AssistantTurn:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("deepseek returned no choices") from None

        raw_calls = message.get("tool_calls") or []
        tool_calls: List[ToolCall] = []
        for call in raw_calls:
            function = call.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=call.get("id", ""),
                    name=function.get("name", ""),
                    arguments=function.get("arguments", "") or "",
                )
            )
        return AssistantTurn(content=message.get("content"), tool_calls=tuple(tool_calls))
