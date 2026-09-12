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
from dataclasses import asdict, dataclass
from datetime import date as Date, datetime
from typing import Any, Optional, Sequence, Union

from riji_agent.calendar.parser import CalendarParseError, looks_like_calendar_request
from riji_agent.calendar.service import CalendarError, CalendarService
from riji_agent.drafts.errors import DraftError
from riji_agent.drafts.models import DraftOperation, DraftStatus
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.confirmation import ConfirmationContext, PrivatePreviewScope, preview_hash
from riji_agent.evolution.service import EvolutionError, EvolutionService
from riji_agent.hermes.access import authorize_chat, verify_shared_secret
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.models import GatewayReply, IncomingMessage
from riji_agent.hermes.routing import route_persona
from riji_agent.im.models import IncomingChatMessage
from riji_agent.memory.models import HistoricalMessage, SessionMessage, session_key
from riji_agent.memory.service import MemoryService
from riji_agent.memory.store import MemoryStore
from riji_agent.memory.worker import MemoryWorker
from riji_agent.models.types import LLMError
from riji_agent.personas.context import AssembledContext, build_context
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
_DRAFT_DATE_RE = re.compile(r"草稿[（(](\d{4}-\d{2}-\d{2})[）)]")
_INLINE_SECTION_RE = re.compile(r"将在\s+([^\s:：]+)\s+追加")
_WRITE_VERIFICATION_PHRASES = (
    "有没有写入",
    "是否写入",
    "正确写入",
    "写入成功",
    "有没有保存",
    "是否保存",
    "保存成功",
    "日记里面没有看到",
    "日记里没有看到",
    "日记里面没看到",
    "日记里没看到",
    "文档里面没有看到",
    "文档里没有看到",
    "文档里面没看到",
    "文档里没看到",
    "存进去",
    "写进去",
    "有没有录入",
    "是否录入",
    "录入成功",
    "实际上我并没有看到",
)
_WRITE_SUCCESS_CLAIMS = ("已写入", "已经写入", "正确写入", "保存成功")
_LOG = logging.getLogger("riji_agent.hermes.gateway")
_ISO_DATE_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)?")
_DAY_RE = re.compile(r"(?P<day>\d{1,2})\s*(?:日|号)")
_NOT_X_BUT_Y_RE = re.compile(
    r"不是\s*(?P<old>.+?)\s*[，,、\s]*(?:而?是|应该是|改成)\s*(?P<new>[^。；;\n]+)"
)
_CHANGE_X_TO_Y_RE = re.compile(
    r"把\s*(?P<old>.+?)\s*改成\s*(?P<new>[^。；;\n]+)"
)


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
    if stripped.startswith(("帮我记住", "帮忙记住")):
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
    stripped = text.strip()
    if not stripped:
        return False
    if _extract_text_replacements(stripped):
        return True
    lowered = stripped.lower()
    has_correction = any(word in lowered for word in ("不对", "错", "不是", "纠正", "改成"))
    has_date_or_section = any(
        word in lowered for word in ("今天", "日期", "日记日期", "notes", "note")
    ) or bool(_ISO_DATE_RE.search(stripped) or _MONTH_DAY_RE.search(stripped) or _DAY_RE.search(stripped))
    return has_correction and has_date_or_section


def _extract_text_replacements(text: str) -> tuple[tuple[str, str], ...]:
    replacements = []
    for pattern in (_NOT_X_BUT_Y_RE, _CHANGE_X_TO_Y_RE):
        for match in pattern.finditer(text):
            old = _clean_replacement_part(match.group("old"))
            new = _clean_replacement_part(match.group("new"))
            if old and new and old != new:
                replacements.append((old, new))
    return tuple(replacements)


def _clean_replacement_part(value: str) -> str:
    return value.strip(" \t\n\r：:，,、。；;！!？?")


def _has_date_or_section_correction(text: str) -> bool:
    stripped = text.strip().lower()
    return any(word in stripped for word in ("今天", "日期", "日记日期", "notes", "note")) or bool(
        _ISO_DATE_RE.search(stripped) or _MONTH_DAY_RE.search(stripped) or _DAY_RE.search(stripped)
    )


def _apply_text_replacements(content: str, replacements: Sequence[tuple[str, str]]) -> str:
    corrected = content
    for old, new in replacements:
        corrected = corrected.replace(old, new)
    return corrected


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


def reply_requests_draft_confirmation(text: str) -> bool:
    return "草稿" in text and bool(re.search(
        r"(?:^|[，,。；;！？\n])\s*(?:请(?:你)?|现在|直接|只需)?\s*"
        r"(?:回复|发送|输入|点击|选择)\s*[「“\"'‘【]*\s*确认保存", text
    ))


def is_draft_verification_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    # Status questions can contain a fast-write trigger (e.g. 记到日记).
    # An explicit new-entry prefix still takes precedence over quoted content.
    explicit_entry = compact.startswith(("帮我记", "帮忙记", "记录一下", "记一下"))
    write_question = re.search(
        r"(?:记录|记到|记下|写入|保存|录入|写到).{0,16}"
        r"(?:了吗|了么|没有|没看到|找不到|成功|是否|有没有)", compact
    )
    has_question = any(term in compact for term in (
        "吗", "么", "是否", "有没有", "没看到", "找不到", "？", "?",
    ))
    if write_question and has_question and not explicit_entry:
        return True
    if parse_fast_draft_request(text) is not None:
        return False
    if any(phrase in compact for phrase in _WRITE_VERIFICATION_PHRASES):
        return True
    has_write_status = any(
        term in compact for term in ("写入", "保存", "录入", "存进去", "写进去")
    )
    has_missing_status = any(
        term in compact for term in ("没有", "没", "未", "不在", "找不到")
    )
    has_verification_context = any(
        term in compact for term in ("日记", "文档", "确认", "检查", "看下", "查看")
    )
    return has_write_status and has_missing_status and has_verification_context


def parse_draft_preview_reply(
    text: str,
) -> Optional[tuple[Date, tuple[DraftOperation, ...]]]:
    """Recover a model-rendered preview so the gateway can make it real.

    This is a guardrail for rare cases where the model writes a draft-looking
    response instead of calling ``draft_daily_entry``. The gateway still creates
    a real pending draft before it ever asks the user to confirm.
    """
    match = _DRAFT_DATE_RE.search(text)
    if match is None:
        return None
    try:
        target = Date.fromisoformat(match.group(1))
    except ValueError:
        return None

    current_section = _inline_section(text) or _DEFAULT_DRAFT_SECTION
    operations: list[DraftOperation] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and "]" in line:
            current_section = (
                line[1 : line.index("]")].strip() or _DEFAULT_DRAFT_SECTION
            )
            continue
        content = _bullet_content(line)
        if content:
            operations.append(DraftOperation(current_section, content))
    if not operations:
        return None
    return target, tuple(operations)


def _inline_section(text: str) -> Optional[str]:
    match = _INLINE_SECTION_RE.search(text)
    return match.group(1).strip() if match else None


def _bullet_content(line: str) -> Optional[str]:
    markers = ("- ", "* ", "• ")
    for marker in markers:
        if line.startswith(marker):
            return line[len(marker) :].strip() or None
    return None


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


def _context_material(context: AssembledContext) -> tuple[Any, ...]:
    """Freeze permission-relevant context while ignoring retrieval progress."""
    memories = tuple(
        tuple({key: value for key, value in asdict(item).items() if key != "score"}
              for item in sorted(items, key=lambda item: str(item.id)))
        for items in (context.shared_memories, context.persona_memories)
    )
    return (
        context.persona.system_prompt, context.persona.answer_boundaries,
        context.persona.allowed_tools, dict(context.preferences), memories,
    )


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
        memory_service: Optional[MemoryService] = None,
        memory_worker: Optional[MemoryWorker] = None,
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
        self.memory_service = memory_service
        self.memory_worker = memory_worker
        self._default_persona = default_persona
        self._lock = threading.Lock()

    def handle(
        self, shared_secret: str, message: Union[IncomingChatMessage, IncomingMessage]
    ) -> GatewayReply:
        message = _normalize_message(message)
        # Gate 1 + 2: caller identity and chat authorization.
        verify_shared_secret(shared_secret, self._secret)
        authorize_chat(message.user_id, message.chat_type, self._allowed)
        from riji_agent.mentors.legacy_route import route_host_message
        discussion_reply = route_host_message(getattr(self, "mentor_runtime", None), message)
        if discussion_reply is not None:
            return discussion_reply

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
                return GatewayReply(
                    uuid.uuid4().hex, current, reply, deduplicated=False
                )

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

                if (is_draft_verification_request(message.text)
                        or self._is_draft_status_followup(message, current)):
                    return self._verify_latest_draft(message, current)

                draft_content = parse_fast_draft_request(message.text)
                if draft_content is not None:
                    return self._create_fast_draft(message, current, draft_content)

            try:
                route = route_persona(
                    message.text, registry=self._registry, current_persona=current
                )
            except UnknownPersonaError:
                reply = self._persona_help(current, prefix="未识别的导师。")
                self._events.record(message.event_id, current, reply)
                return GatewayReply(
                    uuid.uuid4().hex, current, reply, deduplicated=False
                )

            if route.persist:
                self._store.set_preference(
                    user, _CURRENT_PERSONA_PREF, route.persona_id
                )
                if not route.text:
                    reply = self._persona_switch_reply(route.persona_id)
                    self._events.record(message.event_id, route.persona_id, reply)
                    return GatewayReply(
                        uuid.uuid4().hex, route.persona_id, reply, deduplicated=False
                    )

            return self._respond(message, route.persona_id, route.text)

    # --------------------------------------------------------------- internals

    def _is_draft_status_followup(
        self, message: IncomingChatMessage, persona_id: str
    ) -> bool:
        compact = re.sub(r"\s+", "", message.text).strip("。？?！!")
        if compact not in {"为什么没有创建", "为什么没创建", "为什么没有保存", "怎么回事"}:
            return False
        history = self._store.get_session_history(
            message.user_id, persona_id, message.chat_id, limit=1,
        )
        return bool(history and history[-1].role == "assistant" and any(
            term in history[-1].content
            for term in ("可确认草稿", "待确认的草稿", "写入失败", "校验失败")
        ))

    def _record_draft_reply(
        self, message: IncomingChatMessage, persona_id: str, reply: str
    ) -> GatewayReply:
        """Keep deterministic outcomes in the same scoped history as model turns."""
        self._store.append_message(
            message.user_id, persona_id, message.chat_id, "user", message.text,
        )
        self._store.append_message(
            message.user_id, persona_id, message.chat_id, "assistant", reply,
        )
        self._events.record(message.event_id, persona_id, reply)
        return GatewayReply(uuid.uuid4().hex, persona_id, reply, deduplicated=False)

    @staticmethod
    def _private_scope(message: IncomingChatMessage) -> PrivatePreviewScope:
        return PrivatePreviewScope(
            user_id=message.user_id,
            conversation_id=message.conversation_id or f"legacy:{message.chat_id}",
            platform=message.platform,
            app_binding_id=message.app_binding_id,
            chat_id=message.chat_id,
            chat_type=message.chat_type,
        )

    def _bind_draft_preview(self, message: IncomingChatMessage, draft_id: str) -> None:
        self._draft_service.bind_preview(draft_id, self._private_scope(message), message.event_id)

    def _respond(
        self, message: IncomingChatMessage, persona_id: str, question: str
    ) -> GatewayReply:
        user, chat = message.user_id, message.chat_id
        request_id = uuid.uuid4().hex
        assembled = build_context(
            self._store,
            self._registry,
            user_id=user,
            persona_id=persona_id,
            chat_id=chat,
            memory_service=self.memory_service,
            query=question,
        )
        context = ToolContext(
            request_id=request_id,
            session_id=session_key(user, persona_id, chat),
            feishu_user_id=user,
            persona_id=persona_id,
            allowed_tools=assembled.persona.allowed_tools,
            ai_discussion_history=any(item.content_type != "conversation" for item in assembled.history),
        )

        source_message = self._store.append_message(user, persona_id, chat, "user", question)
        started = time.perf_counter()
        expected_context = _context_material(assembled)

        def check_context() -> None:
            current = build_context(
                self._store, self._registry, user_id=user, persona_id=persona_id,
                chat_id=chat, memory_service=self.memory_service, query=question,
            )
            if _context_material(current) != expected_context:
                raise LLMError("chat_context_changed")

        guarded = getattr(self._responder, "respond_guarded", None)
        respond = guarded or self._responder.respond
        kwargs = {"before_send": check_context} if guarded is not None else {}
        reply = respond(context, assembled.system_prompt, assembled.history, question,
                        allowed_tools=assembled.persona.allowed_tools, **kwargs)
        reply = self._block_unverified_write_claim(message, persona_id, reply)
        reply = self._ensure_confirmable_draft_reply(message, persona_id, reply, context=context)
        _LOG.info(
            "gateway responder completed request_id=%s persona=%s elapsed_ms=%.1f",
            request_id,
            persona_id,
            (time.perf_counter() - started) * 1000,
        )
        ai_result = self._ai_result_context(context)
        if ai_result:
            reply = "【AI 讨论资料整理；不代表本人经历】\n" + reply
        self._store.append_message(user, persona_id, chat, "assistant", reply,
                                   content_type="ai_discussion_result" if ai_result else "conversation")
        self._events.record(message.event_id, persona_id, reply)
        self._capture_source(source_message, request_id)
        audio = None
        if _requests_voice_reply(message.text):
            audio = self._synthesize_voice_reply(
                reply,
                request_id,
                voice=assembled.persona.voice_for(self._voice_provider_id()),
            )
        return GatewayReply(request_id, persona_id, reply, deduplicated=False, audio=audio)

    def _capture_source(self, source: HistoricalMessage, request_id: str) -> None:
        if self.memory_service is None:
            return
        self.memory_service.enqueue_capture(
            source_request_id=request_id,
            user_id=source.user_id,
            persona_id=source.persona_id,
            session_id=source.session_id,
            content=source.content,
            source_message_id=source.id,
            source_created_at=source.created_at,
        )
        if self.memory_worker is not None:
            self.memory_worker.wake()

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

    def _block_unverified_write_claim(
        self, message: IncomingChatMessage, persona_id: str, reply: str
    ) -> str:
        if not is_draft_verification_request(message.text):
            return reply
        if not any(claim in reply for claim in _WRITE_SUCCESS_CLAIMS):
            return reply
        if self._draft_service is None:
            return "当前没有可用的本地草稿服务，因此不能声称已经写入。"
        result = self._draft_service.verify_latest_commit(
            user_id=message.user_id,
            session_id=session_key(message.user_id, persona_id, message.chat_id),
        )
        if result is not None and result.verified:
            return reply
        _LOG.warning("blocked unverified journal write success claim")
        return "没有找到可从目标日记文件核验的已提交草稿，因此不能声称已经写入。"

    def _ai_result_context(self, context: ToolContext) -> bool:
        ai_evidence = getattr(self._responder, "has_ai_discussion_evidence", None)
        return context.ai_discussion_history or bool(callable(ai_evidence) and ai_evidence(context.request_id))

    def _ensure_confirmable_draft_reply(
        self, message: IncomingChatMessage, persona_id: str, reply: str, *, context: ToolContext | None = None
    ) -> str:
        if self._draft_service is None:
            return reply

        user, chat = message.user_id, message.chat_id
        session_id = session_key(user, persona_id, chat)
        existing = self._draft_service.get_latest_awaiting_for_session(session_id)
        requests_confirmation = reply_requests_draft_confirmation(reply)
        if requests_confirmation and context is not None and self._ai_result_context(context):
            return "本轮参考了 AI 讨论资料，不能把整理结果保存为本人经历。请在对应讨论选择转交保存，私聊预览独立 AI 结果块后确认。"
        if existing is not None and (requests_confirmation or not self._draft_service.has_preview_binding(existing.draft_id)):
            self._bind_draft_preview(message, existing.draft_id)
            canonical = self._draft_service.render_preview(existing)
            return reply if canonical in reply else reply + "\n" + canonical

        if not requests_confirmation:
            return reply

        parsed = parse_draft_preview_reply(reply)
        if parsed is None:
            _LOG.warning("blocked draft confirmation reply without a pending draft")
            return "我没有成功创建可确认草稿。请重新发送「帮我记录：...」，我会生成真正可保存的草稿。"

        target_date, operations = parsed
        preview = self._draft_service.create_draft(
            user_id=user,
            session_id=session_id,
            persona_id=persona_id,
            operations=operations,
            target_date=target_date,
        )
        _LOG.info(
            "materialized model-rendered draft preview draft_id=%s", preview.draft_id
        )
        self._bind_draft_preview(message, preview.draft_id)
        return preview.preview_text

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
        self._bind_draft_preview(message, preview.draft_id)
        reply = preview.preview_text
        source_message = self._store.append_message(user, persona_id, chat, "user", message.text)
        self._store.append_message(user, persona_id, chat, "assistant", reply)
        self._events.record(message.event_id, persona_id, reply)
        self._capture_source(source_message, request_id)
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
        if any(operation.provenance is not None for operation in previous.operations):
            return self._record_draft_reply(message, persona_id,
                "AI 讨论结果请在专用转交预览中使用 /修改转交 或 /转交日期，修改后重新确认，不能转成普通日记草稿。")

        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        has_date_or_section_correction = _has_date_or_section_correction(message.text)
        corrected_date = (
            self._corrected_date(message.text)
            if has_date_or_section_correction
            else previous.target_date
        )
        replacements = _extract_text_replacements(message.text)
        corrected_ops = tuple(
            DraftOperation(
                _NOTES_SECTION if has_date_or_section_correction else operation.section,
                _apply_text_replacements(operation.content, replacements),
            )
            for operation in previous.operations
        )
        if not has_date_or_section_correction and corrected_ops == previous.operations:
            return None
        preview = self._draft_service.create_draft(
            user_id=user,
            session_id=session_id,
            persona_id=persona_id,
            operations=corrected_ops,
            target_date=corrected_date,
        )
        if previous_was_awaiting:
            self._draft_service.cancel_draft(previous.draft_id, user_id=user)
        self._bind_draft_preview(message, preview.draft_id)
        reply = "已按你的纠正重新起草：\n" + preview.preview_text
        source_message = self._store.append_message(user, persona_id, chat, "user", message.text)
        self._store.append_message(user, persona_id, chat, "assistant", reply)
        self._events.record(message.event_id, persona_id, reply)
        self._capture_source(source_message, request_id)
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
        self,
        message: IncomingChatMessage,
        persona_id: str,
        draft_id: Optional[str] = None,
    ) -> GatewayReply:
        user, chat = message.user_id, message.chat_id
        if draft_id is not None:
            # Explicit id: works across persona switches. Treat a draft that is
            # absent or owned by someone else identically, so we never disclose
            # another user's drafts.
            draft = self._draft_service.get_draft(draft_id)
            if draft is None or draft.user_id != user:
                reply = "未找到该草稿（可能已过期或不属于你）。"
                return self._record_draft_reply(message, persona_id, reply)
        else:
            draft = self._draft_service.get_latest_awaiting_for_session(
                session_key(user, persona_id, chat)
            )
            if draft is None:
                latest = self._draft_service.get_latest_for_session(
                    session_key(user, persona_id, chat)
                )
                if latest is not None and latest.status is DraftStatus.COMMITTED:
                    return self._verify_latest_draft(message, persona_id)
                reply = "没有待确认的草稿。"
                return self._record_draft_reply(message, persona_id, reply)

        try:
            if not self._draft_service.has_preview_binding(draft.draft_id):
                if draft.session_id != session_key(user, draft.persona_id, chat):
                    return self._record_draft_reply(message, persona_id, "请回到展示草稿的私聊重新预览和确认。")
                self._bind_draft_preview(message, draft.draft_id)
                return self._record_draft_reply(
                    message, persona_id, "请核对这份重新展示的草稿，再回复「确认保存」：\n"
                    + self._draft_service.render_preview(draft),
                )
            confirmation = ConfirmationContext(
                scope=self._private_scope(message), draft_id=draft.draft_id,
                preview_hash=preview_hash(draft), event_id=message.event_id, token=draft.token,
            )
            result = self._draft_service.commit_draft(
                draft.draft_id, user_id=user, token=draft.token, confirmation=confirmation,
            )
            reply = (
                f"已写入并重新读取校验 [[{result.source_id}]]"
                f"（{result.target_date.isoformat()}，{'、'.join(result.sections)} 区块）。"
            )
        except DraftError as exc:
            reply = self._draft_error_reply(exc)
        except OSError:
            _LOG.warning("draft commit failed with filesystem error")
            reply = "写入失败：本地日记文件暂时不可读写，请稍后重试。"
        return self._record_draft_reply(message, persona_id, reply)

    def _verify_latest_draft(
        self, message: IncomingChatMessage, persona_id: str
    ) -> GatewayReply:
        latest = self._draft_service.get_latest_for_session(
            session_key(message.user_id, persona_id, message.chat_id)
        )
        if latest is not None and latest.status is not DraftStatus.COMMITTED:
            reply = "最近这份草稿尚未成功提交；之前的保存回执不代表这份也已保存。"
            if latest.status is DraftStatus.AWAITING:
                reply += "\n" + self._draft_service.render_preview(latest)
            return self._record_draft_reply(message, persona_id, reply)
        try:
            result = self._draft_service.ensure_latest_commit(
                user_id=message.user_id,
                session_id=session_key(message.user_id, persona_id, message.chat_id),
            )
        except (DraftError, OSError):
            _LOG.warning("confirmed draft repair failed")
            reply = "检测到已确认内容缺失，但自动恢复没有通过连续校验；不会误报保存成功。"
            return self._record_draft_reply(message, persona_id, reply)
        if result is None:
            reply = (
                "没有找到该会话可核验的已提交草稿；"
                "为避免夹带旧日记内容，本次不会自动生成或补录新草稿。"
            )
        elif result.repaired:
            reply = (
                f"检测到已确认内容缺失，已自动恢复并连续校验 "
                f"[[{result.source_id}]]（{result.target_date.isoformat()}），"
                "无需再次确认保存。"
            )
        elif result.verified:
            reply = (
                f"已从目标日记文件重新读取并校验，内容确实存在于 "
                f"[[{result.source_id}]]（{result.target_date.isoformat()}）。"
            )
        else:
            reply = (
                "重新读取目标日记文件后，未找到这次草稿的完整内容。"
                "因此不能确认写入成功，也不应以之前的成功回复为准。"
            )
        return self._record_draft_reply(message, persona_id, reply)

    @staticmethod
    def _draft_error_reply(exc: DraftError) -> str:
        messages = {
            "token_expired": "草稿已超过 30 分钟时效，请重新生成。",
            "not_awaiting": "该草稿已处理过，未重复写入。",
            "section_not_found": "找不到对应的日记区块，已保留草稿，请调整后重试。",
            "template_not_found": "缺少日记模板，无法新建当天日记。",
            "write_verification_failed": "写入后重新读取校验失败，未确认保存成功；草稿已保留。",
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
            lines.append(
                f"- {persona.name}（{persona.persona_id}）：{persona.description}"
            )
        lines.append("")
        lines.append(
            "切换默认导师：发送 `/导师 导师名`，例如 `/导师 王阳明` 或 `/导师 温柔回顾者`。"
        )
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


def _normalize_message(
    message: Union[IncomingChatMessage, IncomingMessage],
) -> IncomingChatMessage:
    if isinstance(message, IncomingChatMessage):
        return message
    return message.to_chat_message()


def _is_persona_help_request(text: str) -> bool:
    stripped = text.strip()
    if stripped in _PERSONA_HELP_COMMANDS:
        return True
    return any(keyword in stripped for keyword in _PERSONA_HELP_KEYWORDS)
