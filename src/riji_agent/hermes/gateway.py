"""The Hermes gateway: authenticate, authorize, dedupe, route, respond.

Hermes only ever speaks to this HTTP boundary; it never touches the vault, the
index or the database directly. The gateway passes the model nothing but a
request context, the persona system prompt and the user's text.
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional, Sequence

from riji_agent.drafts.errors import DraftError
from riji_agent.drafts.service import DraftService
from riji_agent.hermes.access import authorize_chat, verify_shared_secret
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.models import GatewayReply, IncomingMessage
from riji_agent.hermes.routing import route_persona
from riji_agent.memory.models import SessionMessage, session_key
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.context import build_context
from riji_agent.personas.models import UnknownPersonaError
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import ToolContext

_CURRENT_PERSONA_PREF = "current_persona"
_CONFIRM_COMMANDS = {"确认保存", "确认写入", "/确认", "确认"}


class Responder:
    """Protocol: turn a question into a reply within a persona's context."""

    def respond(
        self,
        context: ToolContext,
        system_prompt: str,
        history: Sequence[SessionMessage],
        question: str,
    ) -> str:  # pragma: no cover - interface only
        raise NotImplementedError


class HermesGateway:
    def __init__(
        self,
        *,
        hermes_secret: str,
        allowed_user_ids,
        registry: PersonaRegistry,
        store: MemoryStore,
        events: EventLog,
        responder: Responder,
        draft_service: Optional[DraftService] = None,
        default_persona: str = "gentle_reviewer",
    ) -> None:
        self._secret = hermes_secret
        self._allowed = allowed_user_ids
        self._registry = registry
        self._store = store
        self._events = events
        self._responder = responder
        self._draft_service = draft_service
        self._default_persona = default_persona
        self._lock = threading.Lock()

    def handle(self, shared_secret: str, message: IncomingMessage) -> GatewayReply:
        # Gate 1 + 2: caller identity and chat authorization.
        verify_shared_secret(shared_secret, self._secret)
        authorize_chat(message.feishu_user_id, message.chat_type, self._allowed)

        user = message.feishu_user_id
        with self._lock:
            seen = self._events.get(message.event_id)
            if seen is not None:
                return GatewayReply(
                    request_id=uuid.uuid4().hex,
                    persona_id=seen.persona_id,
                    text=seen.reply,
                    deduplicated=True,
                )

            current = self._store.get_preferences(user).get(
                _CURRENT_PERSONA_PREF, self._default_persona
            )

            # Explicit, user-driven commit: the model can never confirm a draft.
            if self._draft_service is not None and message.text.strip() in _CONFIRM_COMMANDS:
                return self._confirm_draft(message, current)

            try:
                route = route_persona(message.text, registry=self._registry, current_persona=current)
            except UnknownPersonaError:
                reply = self._unknown_persona_help()
                self._events.record(message.event_id, current, reply)
                return GatewayReply(uuid.uuid4().hex, current, reply, deduplicated=False)

            if route.persist:
                self._store.set_preference(user, _CURRENT_PERSONA_PREF, route.persona_id)

            return self._respond(message, route.persona_id, route.text)

    # --------------------------------------------------------------- internals

    def _respond(self, message: IncomingMessage, persona_id: str, question: str) -> GatewayReply:
        user, chat = message.feishu_user_id, message.chat_id
        request_id = uuid.uuid4().hex
        assembled = build_context(
            self._store, self._registry, user_id=user, persona_id=persona_id, chat_id=chat
        )
        context = ToolContext(
            request_id=request_id,
            session_id=session_key(user, persona_id, chat),
            feishu_user_id=user,
            persona_id=persona_id,
        )

        self._store.append_message(user, persona_id, chat, "user", question)
        reply = self._responder.respond(context, assembled.system_prompt, assembled.history, question)
        self._store.append_message(user, persona_id, chat, "assistant", reply)
        self._events.record(message.event_id, persona_id, reply)
        return GatewayReply(request_id, persona_id, reply, deduplicated=False)

    def _confirm_draft(self, message: IncomingMessage, persona_id: str) -> GatewayReply:
        user, chat = message.feishu_user_id, message.chat_id
        sk = session_key(user, persona_id, chat)
        draft = self._draft_service.get_latest_awaiting_for_session(sk)
        if draft is None:
            reply = "没有待确认的草稿。"
        else:
            try:
                result = self._draft_service.commit_draft(
                    draft.draft_id, user_id=user, token=draft.token
                )
                reply = f"已写入 [[{result.source_id}]]（{result.target_date.isoformat()}）。"
            except DraftError as exc:
                reply = self._draft_error_reply(exc)
        self._events.record(message.event_id, persona_id, reply)
        return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)

    @staticmethod
    def _draft_error_reply(exc: DraftError) -> str:
        messages = {
            "token_expired": "草稿已超过 30 分钟时效，请重新生成。",
            "not_awaiting": "该草稿已处理过，未重复写入。",
            "section_not_found": "找不到对应的日记区块，已保留草稿，请调整后重试。",
            "template_not_found": "缺少日记模板，无法新建当天日记。",
            "wrong_user": "只能由本人确认。",
        }
        return messages.get(exc.code.value, f"写入失败：{exc.message}")

    def _unknown_persona_help(self) -> str:
        names = "、".join(f"{p.name}（{p.persona_id}）" for p in self._registry.all())
        return f"未识别的导师。可用导师：{names}。用「/导师 名称」切换。"
