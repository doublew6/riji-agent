from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.gateway import HermesGateway
from riji_agent.hermes.models import IncomingMessage
from riji_agent.im.models import IncomingChatMessage
from riji_agent.memory.store import MemoryStore
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.voice.models import VoiceAttachment

SECRET = "top-secret-shared"


class FakeResponder:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def respond(self, context, system_prompt, history, question, allowed_tools=()) -> str:
        self.calls.append({"persona": context.persona_id, "question": question})
        return f"[{context.persona_id}] {question}"


class FakeVoiceReplyService:
    provider_id = "macos_say"

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def synthesize_reply(self, *, text: str, request_id: str, voice: Optional[str] = None):
        self.calls.append({"text": text, "request_id": request_id, "voice": voice})
        return VoiceAttachment(path="/tmp/riji-agent-voice/reply.opus", mime_type="audio/ogg")


class FakeMeloVoiceReplyService(FakeVoiceReplyService):
    provider_id = "melotts"


def _msg(text: str, *, event_id: str = "e1", user: str = "ou_1", chat: str = "c1", chat_type: str = "p2p") -> IncomingMessage:
    return IncomingMessage(event_id=event_id, feishu_user_id=user, chat_id=chat, chat_type=chat_type, text=text)


@pytest.fixture
def setup(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem.sqlite3")
    events = EventLog(tmp_path / "events.sqlite3")
    responder = FakeResponder()
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=store,
        events=events,
        responder=responder,
    )
    yield gateway, store, responder
    store.close()
    events.close()


def test_authorized_private_chat_gets_reply(setup) -> None:
    gateway, _store, responder = setup
    reply = gateway.handle(SECRET, _msg("你好"))
    assert reply.persona_id == "gentle_reviewer"  # default
    assert reply.deduplicated is False
    assert len(responder.calls) == 1


def test_gateway_accepts_neutral_im_message(setup) -> None:
    gateway, _store, responder = setup
    message = IncomingChatMessage(
        event_id="neutral-1",
        user_id="ou_1",
        chat_id="c1",
        chat_type="p2p",
        text="你好",
        platform="feishu",
    )

    reply = gateway.handle(SECRET, message)

    assert reply.persona_id == "gentle_reviewer"
    assert responder.calls[-1]["question"] == "你好"


def test_bad_secret_is_rejected(setup) -> None:
    gateway, _store, responder = setup
    with pytest.raises(AuthError) as err:
        gateway.handle("wrong", _msg("你好"))
    assert err.value.code is AuthErrorCode.UNAUTHENTICATED
    assert responder.calls == []


def test_group_chat_is_denied(setup) -> None:
    gateway, _store, responder = setup
    with pytest.raises(AuthError) as err:
        gateway.handle(SECRET, _msg("你好", chat_type="group"))
    assert err.value.code is AuthErrorCode.GROUP_CHAT_DENIED
    assert responder.calls == []


def test_non_whitelisted_user_is_denied(setup) -> None:
    gateway, _store, responder = setup
    with pytest.raises(AuthError) as err:
        gateway.handle(SECRET, _msg("你好", user="ou_evil"))
    assert err.value.code is AuthErrorCode.FORBIDDEN_USER


def test_switching_three_personas_via_commands(setup) -> None:
    gateway, store, _responder = setup
    assert gateway.handle(SECRET, _msg("/导师 温柔回顾者", event_id="e1")).persona_id == "gentle_reviewer"
    assert gateway.handle(SECRET, _msg("/导师 直率教练", event_id="e2")).persona_id == "blunt_coach"
    assert gateway.handle(SECRET, _msg("/导师 未来的我", event_id="e3")).persona_id == "future_self"
    # last switch persisted as the current persona
    assert store.get_preferences("ou_1")["current_persona"] == "future_self"


def test_at_mention_does_not_change_current_persona(setup) -> None:
    gateway, store, _responder = setup
    gateway.handle(SECRET, _msg("/导师 直率教练", event_id="e1"))
    one_shot = gateway.handle(SECRET, _msg("@温柔回顾者 安慰我", event_id="e2"))
    assert one_shot.persona_id == "gentle_reviewer"
    assert store.get_preferences("ou_1")["current_persona"] == "blunt_coach"  # unchanged


def test_voice_reply_uses_selected_persona_voice(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.sqlite3")
    events = EventLog(tmp_path / "events.sqlite3")
    voice = FakeVoiceReplyService()
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=store,
        events=events,
        responder=FakeResponder(),
        voice_reply_service=voice,
    )

    reply = gateway.handle(SECRET, _msg("/导师 直率教练 给一句建议", event_id="voice-persona"))

    assert reply.audio is not None
    assert reply.persona_id == "blunt_coach"
    assert voice.calls == [
        {
            "text": "[blunt_coach] 给一句建议",
            "request_id": reply.request_id,
            "voice": "Eddy (中文（中国大陆）)",
        }
    ]
    store.close()
    events.close()


def test_voice_reply_uses_provider_specific_persona_voice(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.sqlite3")
    events = EventLog(tmp_path / "events.sqlite3")
    voice = FakeMeloVoiceReplyService()
    gateway = HermesGateway(
        hermes_secret=SECRET,
        allowed_user_ids={"ou_1"},
        registry=PersonaRegistry(),
        store=store,
        events=events,
        responder=FakeResponder(),
        voice_reply_service=voice,
    )

    reply = gateway.handle(SECRET, _msg("/导师 王阳明 给一句建议", event_id="voice-melo"))

    assert reply.audio is not None
    assert reply.persona_id == "wang_yangming"
    assert voice.calls == [
        {
            "text": "[wang_yangming] 给一句建议",
            "request_id": reply.request_id,
            "voice": "ZH",
        }
    ]
    store.close()
    events.close()


def test_unknown_persona_returns_help(setup) -> None:
    gateway, _store, responder = setup
    reply = gateway.handle(SECRET, _msg("/导师 不存在", event_id="e1"))
    assert "可用导师" in reply.text
    assert "温柔回顾者" in reply.text
    assert "直率教练" in reply.text
    assert "未来的我" in reply.text
    assert "王阳明" in reply.text
    assert responder.calls == []  # no model call for a routing help message


def test_persona_list_question_returns_all_personas_without_model_call(setup) -> None:
    gateway, _store, responder = setup

    reply = gateway.handle(SECRET, _msg("我有哪些导师可以选择？", event_id="persona-help"))

    assert reply.persona_id == "gentle_reviewer"
    assert "当前导师：温柔回顾者" in reply.text
    assert "温柔回顾者" in reply.text
    assert "直率教练" in reply.text
    assert "未来的我" in reply.text
    assert "王阳明" in reply.text
    assert "/导师 王阳明" in reply.text
    assert "@直率教练" in reply.text
    assert "私有对话历史互相隔离" in reply.text
    assert responder.calls == []


def test_persona_switch_guidance_works_from_any_current_persona(setup) -> None:
    gateway, store, responder = setup
    gateway.handle(SECRET, _msg("/导师 直率教练", event_id="switch"))

    reply = gateway.handle(SECRET, _msg("怎么切换导师？", event_id="switch-help"))

    assert reply.persona_id == "blunt_coach"
    assert "当前导师：直率教练" in reply.text
    assert "/导师 温柔回顾者" in reply.text
    assert "@未来的我" in reply.text
    assert store.get_preferences("ou_1")["current_persona"] == "blunt_coach"
    assert responder.calls == []


def test_pure_persona_switch_confirms_without_model_call(setup) -> None:
    gateway, store, responder = setup

    reply = gateway.handle(SECRET, _msg("/导师 直率教练", event_id="pure-switch"))

    assert reply.persona_id == "blunt_coach"
    assert "已切换默认导师：直率教练" in reply.text
    assert store.get_preferences("ou_1")["current_persona"] == "blunt_coach"
    assert responder.calls == []


def test_empty_persona_command_returns_guidance_without_model_call(setup) -> None:
    gateway, _store, responder = setup

    reply = gateway.handle(SECRET, _msg("/导师", event_id="empty-persona"))

    assert "可用导师" in reply.text
    assert "/导师 王阳明" in reply.text
    assert responder.calls == []


def test_duplicate_event_is_idempotent(setup) -> None:
    gateway, _store, responder = setup
    first = gateway.handle(SECRET, _msg("记录这件事", event_id="same"))
    second = gateway.handle(SECRET, _msg("记录这件事", event_id="same"))
    assert second.deduplicated is True
    assert second.text == first.text
    assert len(responder.calls) == 1  # responder ran only once


def test_session_history_is_persisted(setup) -> None:
    gateway, store, _responder = setup
    gateway.handle(SECRET, _msg("今天很累", event_id="e1"))
    history = store.get_session_history("ou_1", "gentle_reviewer", "c1")
    roles = [m.role for m in history]
    assert roles == ["user", "assistant"]
