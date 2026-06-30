"""DeepSeek chat completion provider: the default model adapter.

DeepSeek speaks the OpenAI-compatible wire format, so this is a thin preset over
:class:`OpenAICompatibleProvider` that only fixes the default base URL, model and
the error label. The API key stays inside the object and is never logged.
"""

from __future__ import annotations

from typing import Optional

import httpx

from riji_agent.models.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-reasoner",
        timeout: float = 60.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            client=client,
            provider_label="deepseek",
        )
