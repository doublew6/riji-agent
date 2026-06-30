"""Feishu-to-riji-agent bridge for Hermes.

Hermes' built-in Feishu access would, by default, let Hermes answer Feishu
messages itself. That bypasses the local journal privacy boundary. This bridge
is the thin adapter that instead forwards each Feishu message *verbatim* to
riji-agent's ``/hermes/messages`` endpoint and returns the reply text.

The bridge deliberately does nothing else:

- it does not parse or rewrite the user's text (``/导师 王阳明`` passes through;
  persona switching is riji-agent's job);
- it does not generate its own event id (idempotency stays keyed on the Feishu
  ``event_id`` that riji-agent dedupes on);
- it does not decide who may use journal tools (group chats and non-allowlisted
  users are rejected by riji-agent's 403, not whitelisted here);
- it has no access to the vault, SQLite, the index or any model key.

The shared secret is held inside this object and sent only as the
``X-Hermes-Secret`` header. It is never logged, returned, or included in any
error message surfaced to the caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

import httpx

DEFAULT_RIJI_AGENT_URL = "http://127.0.0.1:8765/hermes/messages"

# Returned to the user when riji-agent cannot be reached or answers with an
# error status. It carries no internal detail and never the shared secret.
_FAILURE_REPLY = "日记助手暂时无法回应，请稍后再试。"
_TIMEOUT_REPLY = "日记助手正在处理这条较复杂的问题，但本次等待超时了。请稍后重试，或把问题拆短一点。"
DEFAULT_TIMEOUT_SECONDS = 240.0


class BridgeConfigError(RuntimeError):
    """Raised when required bridge configuration is missing or invalid."""


@dataclass(frozen=True)
class FeishuMessageEvent:
    """The minimal shape the bridge needs from a Feishu message event.

    Hermes' Feishu payloads are richer than this; the bridge only consumes the
    five fields that riji-agent's contract requires and ignores the rest.
    """

    event_id: str
    feishu_user_id: str
    chat_id: str
    chat_type: str
    text: str

    @classmethod
    def from_mapping(cls, event: Mapping[str, object]) -> "FeishuMessageEvent":
        """Build an event from a plain dict, e.g. a Hermes callback payload.

        Missing keys default to empty strings so a malformed payload still
        produces a well-formed (and thus rejectable-by-riji-agent) request,
        rather than raising and leaking internals.
        """
        return cls(
            event_id=str(event.get("event_id", "")),
            feishu_user_id=str(event.get("feishu_user_id", "")),
            chat_id=str(event.get("chat_id", "")),
            chat_type=str(event.get("chat_type", "")),
            text=str(event.get("text", "")),
        )


class HermesFeishuBridge:
    """Forward Feishu message events to riji-agent and return the reply text."""

    def __init__(
        self,
        *,
        riji_agent_url: str = DEFAULT_RIJI_AGENT_URL,
        shared_secret: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: Optional[httpx.Client] = None,
    ) -> None:
        if not riji_agent_url:
            raise BridgeConfigError("riji_agent_url must not be empty")
        if not shared_secret:
            raise BridgeConfigError("shared_secret must not be empty")
        self._url = riji_agent_url
        self._secret = shared_secret
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        client: Optional[httpx.Client] = None,
    ) -> "HermesFeishuBridge":
        """Construct from ``RIJI_AGENT_URL`` and ``HERMES_SHARED_SECRET``.

        ``RIJI_AGENT_URL`` is optional and defaults to the loopback endpoint;
        ``HERMES_SHARED_SECRET`` is required and must match riji-agent's.
        """
        source = os.environ if env is None else env
        secret = source.get("HERMES_SHARED_SECRET", "")
        if not secret:
            raise BridgeConfigError("HERMES_SHARED_SECRET is required")
        url = source.get("RIJI_AGENT_URL") or DEFAULT_RIJI_AGENT_URL
        timeout = float(source.get("RIJI_AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        return cls(riji_agent_url=url, shared_secret=secret, timeout=timeout, client=client)

    def forward(self, event: FeishuMessageEvent) -> str:
        """Forward one Feishu event to riji-agent and return the reply text.

        On any transport error or non-2xx status, returns a safe failure
        message instead of raising, so the secret and internal details never
        reach the Feishu side. Sending the reply back to Feishu is the caller's
        (Hermes') responsibility.
        """
        body = {
            "event_id": event.event_id,
            "feishu_user_id": event.feishu_user_id,
            "chat_id": event.chat_id,
            "chat_type": event.chat_type,
            "text": event.text,
        }
        try:
            response = self._client.post(
                self._url,
                json=body,
                headers={"X-Hermes-Secret": self._secret},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            return _TIMEOUT_REPLY
        except httpx.HTTPError:
            # Covers timeouts, connection errors and non-2xx statuses. We do not
            # attach the exception text to the reply: it may echo headers.
            return _FAILURE_REPLY
        except ValueError:
            # Malformed JSON in an otherwise-2xx response.
            return _FAILURE_REPLY

        reply = data.get("reply")
        if not isinstance(reply, str) or not reply:
            return _FAILURE_REPLY
        return reply
