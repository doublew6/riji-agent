"""The Hermes gateway: authenticate, authorize, dedupe, route, respond.

Hermes only ever speaks to this HTTP boundary; it never touches the vault, the
index or the database directly. The gateway passes the model nothing but a
request context, the persona system prompt and the user's text.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date as Date, datetime
from typing import Optional, Sequence, Union

from riji_agent.calendar.parser import CalendarParseError, looks_like_calendar_request
from riji_agent.calendar.service import CalendarError, CalendarService
from riji_agent.drafts.errors import DraftError
from riji_agent.drafts.models import DraftOperation
from riji_agent.drafts.service import DraftService
from riji_agent.evolution.service import EvolutionError, EvolutionService
from riji_agent.hermes.access import authorize_chat, verify_shared_secret
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.models import GatewayReply, IncomingMessage
from riji_agent.hermes.routing import route_persona
from riji_agent.im.models import IncomingChatMessage
from riji_agent.memory.models import SessionMessage, session_key
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.context import build_context
from riji_agent.personas.models import UnknownPersonaError
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.models import ToolContext
from riji_agent.timezone import local_journal_timezone
from riji_agent.voice.models import VoiceAttachment
from riji_agent.voice.service import VoiceReplyService

_CURRENT_PERSONA_PREF = "current_persona"
_CONFIRM_COMMANDS = {"确认保存", "确认写入", "/确认", "确认"}
_CONFIRM_CALENDAR_COMMANDS = {"确认创建", "确认日程", "/确认日程"}
_CONFIRM_EVOLUTION_COMMANDS = {"确认改进", "/确认改进"}
_REJECT_EVOLUTION_COMMANDS = {"拒绝改进", "取消改进", "/拒绝改进"}
_HERMES_EVOLUTION_PREFIXES = ("/hermes", "hermes：", "hermes:")
_PERSONA_HELP_COMMANDS = {"/导师", "/persona", "/切换", "导师列表"}
_PERSONA_HELP_KEYWORDS = (
    "有哪些导师",
    "导师可以选择",
    "导师列表",
    "怎么切换导师",
    "如何切换导师",
    "切换导师",
)
_FAST_DRAFT_TRIGGERS = (
    "记录一下",
    "记一下",
    "写日记",
    "记到日记",
    "在日记里记录",
    "帮忙记录",
    "帮忙记",
    "帮我记录",
    "帮我记",
)
_VOICE_REPLY_TRIGGERS = (
    "用语音",
    "语音回复",
    "语音回答",
    "发语音",
    "回语音",
    "用声音",
    "声音回复",
    "声音回答",
    "读出来",
    "念出来",
    "朗读",
    "voice reply",
    "reply with voice",
    "audio reply",
    "send voice",
    "read aloud",
)
_VOICE_REPLY_NEGATIONS = (
    "不要用语音",
    "不用语音",
    "别用语音",
    "不要发语音",
    "别发语音",
    "不要声音",
    "不用声音",
    "别用声音",
    "no voice",
    "without voice",
    "text only",
)
_DEFAULT_DRAFT_SECTION = "Notes"
_NOTES_SECTION = "Notes"
_LOG = logging.getLogger("riji_agent.hermes.gateway")
_ISO_DATE_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)?")
_DAY_RE = re.compile(r"(?P<day>\d{1,2})\s*(?:日|号)")


@dataclass(frozen=True)
class ConfirmCommand:
    """A parsed confirmation, optionally targeting a specific draft by id."""

    draft_id: Optional[str]


def parse_confirm_command(text: str) -> Optional[ConfirmCommand]:
    """Recognise a confirmation, with an optional explicit ``draft_id``.

    ``确认保存`` confirms the current session's pending draft; ``确认保存 <id>``
    confirms a specific draft even after the user switched personas. A normal
    message that merely contains 确认 is not a confirmation: the first
    whitespace-delimited token must be an exact confirm keyword.
    """
    parts = text.strip().split()
    if not parts or parts[0] not in _CONFIRM_COMMANDS:
        return None
    draft_id = parts[1] if len(parts) > 1 else None
    return ConfirmCommand(draft_id=draft_id)


def parse_fast_draft_request(text: str) -> Optional[str]:
    """Extract explicit journal-write content without calling the model."""
    stripped = text.strip()
    if not stripped:
        return None
    if not any(trigger in stripped for trigger in _FAST_DRAFT_TRIGGERS):
        return None

    best_idx = -1
    best_trigger = ""
    for trigger in _FAST_DRAFT_TRIGGERS:
        idx = stripped.find(trigger)
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
            best_trigger = trigger
    if best_idx < 0:
        return None

    content = stripped[best_idx + len(best_trigger) :].lstrip(" ：:，,。.！!；;、\n\t")
    if not content or _is_generic_draft_placeholder(content):
        return None
    return content


def _is_generic_draft_placeholder(content: str) -> bool:
    compact = content.strip()
    return compact in {"今天的事", "今天的事情", "这件事", "这个事", "这些事"}


def _requests_voice_reply(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    compact = normalized.replace(" ", "")
    if not normalized:
        return False
    if any(term in normalized or term in compact for term in _VOICE_REPLY_NEGATIONS):
        return False
    return any(term in normalized or term in compact for term in _VOICE_REPLY_TRIGGERS)


def _evolution_request_text(text: str) -> Optional[str]:
    lowered = text.lower()
    for prefix in _HERMES_EVOLUTION_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip(" ：:\n\t") or "分析系统改进建议"
    return None


def is_draft_correction_request(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return False
    has_correction = any(word in stripped for word in ("不对", "错", "不是", "纠正", "改成"))
    has_date_or_section = any(
        word in stripped for word in ("今天", "日期", "日记日期", "notes", "note")
    ) or bool(_ISO_DATE_RE.search(stripped) or _MONTH_DAY_RE.search(stripped) or _DAY_RE.search(stripped))
    return has_correction and has_date_or_section


def _local_today() -> Date:
    return datetime.now(local_journal_timezone()).date()


def _date_or_none(year: int, month: int, day: int) -> Optional[Date]:
    try:
        return Date(year, month, day)
    except ValueError:
        return None


def _extract_corrected_date(text: str, *, today: Optional[Date] = None) -> Date:
    local_today = today or _local_today()
    if match := _ISO_DATE_RE.search(text):
        parsed = _date_or_none(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        if parsed is not None:
            return parsed
    if match := _MONTH_DAY_RE.search(text):
        parsed = _date_or_none(
            local_today.year, int(match.group("month")), int(match.group("day"))
        )
        if parsed is not None:
            return parsed
    if match := _DAY_RE.search(text):
        parsed = _date_or_none(local_today.year, local_today.month, int(match.group("day")))
        if parsed is not None:
            return parsed
    return local_today


class Responder:
    """Protocol: turn a question into a reply within a persona's context."""

    def respond(
        self,
        context: ToolContext,
        system_prompt: str,
        history: Sequence[SessionMessage],
        question: str,
        allowed_tools: Sequence[str] = (),
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
        calendar_service: Optional[CalendarService] = None,
        evolution_service: Optional[EvolutionService] = None,
        voice_reply_service: Optional[VoiceReplyService] = None,
        default_persona: str = "gentle_reviewer",
    ) -> None:
        self._secret = hermes_secret
        self._allowed = allowed_user_ids
        self._registry = registry
        self._store = store
        self._events = events
        self._responder = responder
        self._draft_service = draft_service
        self._calendar_service = calendar_service
        self._evolution_service = evolution_service
        self._voice_reply_service = voice_reply_service
        self._default_persona = default_persona
        self._lock = threading.Lock()

    def handle(
        self, shared_secret: str, message: Union[IncomingChatMessage, IncomingMessage]
    ) -> GatewayReply:
        message = _normalize_message(message)
        # Gate 1 + 2: caller identity and chat authorization.
        verify_shared_secret(shared_secret, self._secret)
        authorize_chat(message.user_id, message.chat_type, self._allowed)

        user = message.user_id
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
            if _is_persona_help_request(message.text):
                reply = self._persona_help(current)
                self._events.record(message.event_id, current, reply)
                return GatewayReply(uuid.uuid4().hex, current, reply, deduplicated=False)

            if self._evolution_service is not None:
                evolution = self._handle_evolution(message, current)
                if evolution is not None:
                    return evolution

            if self._calendar_service is not None:
                calendar = self._handle_calendar(message, current)
                if calendar is not None:
                    return calendar

            # Explicit, user-driven commit: the model can never confirm a draft.
            if self._draft_service is not None:
                confirm = parse_confirm_command(message.text)
                if confirm is not None:
                    return self._confirm_draft(message, current, confirm.draft_id)

                if is_draft_correction_request(message.text):
                    corrected = self._correct_latest_draft(message, current)
                    if corrected is not None:
                        return corrected

                draft_content = parse_fast_draft_request(message.text)
                if draft_content is not None:
                    return self._create_fast_draft(message, current, draft_content)

            try:
                route = route_persona(message.text, registry=self._registry, current_persona=current)
            except UnknownPersonaError:
                reply = self._persona_help(current, prefix="未识别的导师。")
                self._events.record(message.event_id, current, reply)
                return GatewayReply(uuid.uuid4().hex, current, reply, deduplicated=False)

            if route.persist:
                self._store.set_preference(user, _CURRENT_PERSONA_PREF, route.persona_id)
                if not route.text:
                    reply = self._persona_switch_reply(route.persona_id)
                    self._events.record(message.event_id, route.persona_id, reply)
                    return GatewayReply(
                        uuid.uuid4().hex, route.persona_id, reply, deduplicated=False
                    )

            return self._respond(message, route.persona_id, route.text)

    # --------------------------------------------------------------- internals

    def _respond(
        self, message: IncomingChatMessage, persona_id: str, question: str
    ) -> GatewayReply:
        user, chat = message.user_id, message.chat_id
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
        started = time.perf_counter()
        reply = self._responder.respond(
            context,
            assembled.system_prompt,
            assembled.history,
            question,
            allowed_tools=assembled.persona.allowed_tools,
        )
        _LOG.info(
            "gateway responder completed request_id=%s persona=%s elapsed_ms=%.1f",
            request_id,
            persona_id,
            (time.perf_counter() - started) * 1000,
        )
        self._store.append_message(user, persona_id, chat, "assistant", reply)
        self._events.record(message.event_id, persona_id, reply)
        audio = None
        if _requests_voice_reply(message.text):
            audio = self._synthesize_voice_reply(
                reply,
                request_id,
                voice=assembled.persona.voice_for(self._voice_provider_id()),
            )
        return GatewayReply(request_id, persona_id, reply, deduplicated=False, audio=audio)

    def _voice_provider_id(self) -> str:
        if self._voice_reply_service is None:
            return ""
        return getattr(self._voice_reply_service, "provider_id", "")

    def _synthesize_voice_reply(
        self, reply: str, request_id: str, *, voice: Optional[str] = None
    ) -> Optional[VoiceAttachment]:
        if self._voice_reply_service is None:
            return None
        try:
            return self._voice_reply_service.synthesize_reply(
                text=reply,
                request_id=request_id,
                voice=voice,
            )
        except Exception:
            _LOG.warning("voice reply generation failed request_id=%s", request_id, exc_info=True)
            return None

    def _handle_calendar(
        self, message: IncomingChatMessage, persona_id: str
    ) -> Optional[GatewayReply]:
        assert self._calendar_service is not None
        user, chat = message.user_id, message.chat_id
        session_id = session_key(user, persona_id, chat)
        first = message.text.strip().split(maxsplit=1)[0] if message.text.strip() else ""
        if first in _CONFIRM_CALENDAR_COMMANDS:
            try:
                result = self._calendar_service.confirm_latest(
                    user_id=user,
                    session_id=session_id,
                )
            except CalendarError as exc:
                reply = self._calendar_error_reply(exc)
            else:
                if result.journal_source_id:
                    linked = f"\n已关联到 [[{result.journal_source_id}]]。"
                elif result.journal_link_deferred:
                    linked = "\n未来日期不会提前创建日记，到当天再按当时模板记录。"
                else:
                    linked = "\n日程已创建，但未能写入日记关联。"
                reply = f"已创建日程：{result.title}（{result.start_at:%Y-%m-%d %H:%M}）。{linked}"
            self._events.record(message.event_id, persona_id, reply)
            return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)
        if not looks_like_calendar_request(message.text):
            return None
        try:
            draft = self._calendar_service.create_draft_from_text(
                user_id=user,
                session_id=session_id,
                persona_id=persona_id,
                text=message.text,
            )
        except CalendarParseError:
            return None
        reply = self._calendar_service.render_preview(draft)
        self._events.record(message.event_id, persona_id, reply)
        return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)

    def _handle_evolution(
        self, message: IncomingChatMessage, persona_id: str
    ) -> Optional[GatewayReply]:
        assert self._evolution_service is not None
        user, chat = message.user_id, message.chat_id
        session_id = session_key(user, persona_id, chat)
        stripped = message.text.strip()
        first = stripped.split(maxsplit=1)[0] if stripped else ""
        if first in _CONFIRM_EVOLUTION_COMMANDS:
            try:
                self._evolution_service.approve_latest(user_id=user, session_id=session_id)
                reply = "已标记为已批准。具体代码、权限或自动化变更仍需单独实现和审查。"
            except EvolutionError:
                reply = "没有待确认的改进提案。"
            self._events.record(message.event_id, persona_id, reply)
            return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)
        if first in _REJECT_EVOLUTION_COMMANDS:
            try:
                self._evolution_service.reject_latest(user_id=user, session_id=session_id)
                reply = "已拒绝这条改进提案。"
            except EvolutionError:
                reply = "没有待确认的改进提案。"
            self._events.record(message.event_id, persona_id, reply)
            return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)
        request = _evolution_request_text(stripped)
        if request is None:
            return None
        proposal = self._evolution_service.create_proposal(
            user_id=user,
            session_id=session_id,
            request_text=request,
        )
        reply = self._evolution_service.render_preview(proposal)
        self._events.record(message.event_id, persona_id, reply)
        return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)

    @staticmethod
    def _calendar_error_reply(exc: CalendarError) -> str:
        messages = {
            "no_pending_calendar_draft": "没有待确认的日程草稿。",
            "calendar_draft_not_found": "未找到该日程草稿。",
            "calendar_draft_not_awaiting": "该日程草稿已处理过。",
            "calendar_draft_expired": "日程草稿已超过 30 分钟时效，请重新生成。",
            "calendar_provider_disabled": "日历服务尚未启用。",
            "provider_auth_failed": "日历认证失败，请检查本地配置。",
            "provider_permission_denied": "飞书日历权限不足，请在飞书开发者后台开通日历创建权限并发布生效。",
            "provider_create_failed": "创建日程失败，请稍后重试。",
            "provider_attendee_failed": "日程已创建，但未能加入你的飞书日历，请稍后重试或检查参与人权限。",
            "provider_missing_event_id": "创建日程失败：日历服务未返回事件 ID。",
        }
        return messages.get(exc.code, "创建日程失败，请稍后重试。")

    def _create_fast_draft(
        self, message: IncomingChatMessage, persona_id: str, content: str
    ) -> GatewayReply:
        user, chat = message.user_id, message.chat_id
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        preview = self._draft_service.create_draft(
            user_id=user,
            session_id=session_key(user, persona_id, chat),
            persona_id=persona_id,
            operations=[DraftOperation(_DEFAULT_DRAFT_SECTION, content)],
        )
        reply = preview.preview_text
        self._store.append_message(user, persona_id, chat, "user", message.text)
        self._store.append_message(user, persona_id, chat, "assistant", reply)
        self._events.record(message.event_id, persona_id, reply)
        _LOG.info(
            "gateway fast draft completed request_id=%s persona=%s elapsed_ms=%.1f",
            request_id,
            persona_id,
            (time.perf_counter() - started) * 1000,
        )
        return GatewayReply(request_id, persona_id, reply, deduplicated=False)

    def _correct_latest_draft(
        self, message: IncomingChatMessage, persona_id: str
    ) -> Optional[GatewayReply]:
        user, chat = message.user_id, message.chat_id
        session_id = session_key(user, persona_id, chat)
        previous = self._draft_service.get_latest_awaiting_for_session(session_id)
        previous_was_awaiting = previous is not None
        if previous is None:
            previous = self._draft_service.get_latest_for_session(session_id)
        if previous is None or not previous.operations:
            return None

        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        corrected_date = self._corrected_date(message.text)
        corrected_ops = tuple(
            DraftOperation(_NOTES_SECTION, operation.content)
            for operation in previous.operations
        )
        preview = self._draft_service.create_draft(
            user_id=user,
            session_id=session_id,
            persona_id=persona_id,
            operations=corrected_ops,
            target_date=corrected_date,
        )
        if previous_was_awaiting:
            self._draft_service.cancel_draft(previous.draft_id, user_id=user)
        reply = "已按你的纠正重新起草：\n" + preview.preview_text
        self._store.append_message(user, persona_id, chat, "user", message.text)
        self._store.append_message(user, persona_id, chat, "assistant", reply)
        self._events.record(message.event_id, persona_id, reply)
        _LOG.info(
            "gateway corrected draft completed request_id=%s persona=%s elapsed_ms=%.1f",
            request_id,
            persona_id,
            (time.perf_counter() - started) * 1000,
        )
        return GatewayReply(request_id, persona_id, reply, deduplicated=False)

    @staticmethod
    def _corrected_date(text: str) -> Date:
        return _extract_corrected_date(text)

    def _confirm_draft(
        self, message: IncomingChatMessage, persona_id: str, draft_id: Optional[str] = None
    ) -> GatewayReply:
        user, chat = message.user_id, message.chat_id
        if draft_id is not None:
            # Explicit id: works across persona switches. Treat a draft that is
            # absent or owned by someone else identically, so we never disclose
            # another user's drafts.
            draft = self._draft_service.get_draft(draft_id)
            if draft is None or draft.user_id != user:
                reply = "未找到该草稿（可能已过期或不属于你）。"
                self._events.record(message.event_id, persona_id, reply)
                return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)
        else:
            draft = self._draft_service.get_latest_awaiting_for_session(
                session_key(user, persona_id, chat)
            )
            if draft is None:
                reply = "没有待确认的草稿。"
                self._events.record(message.event_id, persona_id, reply)
                return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)

        try:
            result = self._draft_service.commit_draft(
                draft.draft_id, user_id=user, token=draft.token
            )
            reply = f"已写入 [[{result.source_id}]]（{result.target_date.isoformat()}）。"
        except DraftError as exc:
            reply = self._draft_error_reply(exc)
        except OSError:
            _LOG.warning("draft commit failed with filesystem error")
            reply = "写入失败：本地日记文件暂时不可读写，请稍后重试。"
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

    def _persona_help(self, current_persona: str, *, prefix: str = "") -> str:
        current = self._registry.get(current_persona)
        lines = []
        if prefix:
            lines.append(prefix)
        lines.append(f"当前导师：{current.name}（{current.persona_id}）")
        lines.append("")
        lines.append("可用导师：")
        for persona in self._registry.all():
            lines.append(f"- {persona.name}（{persona.persona_id}）：{persona.description}")
        lines.append("")
        lines.append("切换默认导师：发送 `/导师 导师名`，例如 `/导师 王阳明` 或 `/导师 温柔回顾者`。")
        lines.append(
            "只让下一条消息使用某位导师：发送 `@导师名 内容`，例如 `@直率教练 帮我复盘这件事`"
            " 或 `@未来的我 给我一个提醒`。"
        )
        lines.append("各导师的私有对话历史互相隔离；日记事实和已确认长期记忆共享。")
        return "\n".join(lines)

    def _persona_switch_reply(self, persona_id: str) -> str:
        persona = self._registry.get(persona_id)
        return (
            f"已切换默认导师：{persona.name}（{persona.persona_id}）。\n"
            "之后的普通消息会由这位导师回复；也可以用 `@导师名 内容` 临时指定其他导师。"
        )


def _normalize_message(message: Union[IncomingChatMessage, IncomingMessage]) -> IncomingChatMessage:
    if isinstance(message, IncomingChatMessage):
        return message
    return message.to_chat_message()


def _is_persona_help_request(text: str) -> bool:
    stripped = text.strip()
    if stripped in _PERSONA_HELP_COMMANDS:
        return True
    return any(keyword in stripped for keyword in _PERSONA_HELP_KEYWORDS)
