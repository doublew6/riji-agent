"""Device admission and native conversations over the existing worker and policy."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from typing import Callable

from riji_agent.mentors.local_channel import LocalChannel
from riji_agent.mentors.models import Application, ChatBinding, Command, Conversation, Delivery, MentorError, Principal, Source
from riji_agent.mentors.store import key
from riji_agent.mobile.memory import MobileMemory
from riji_agent.mobile.store import MobileError, MobileStore

MENTORS = {"gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练",
           "future_self": "未来的我", "wang_yangming": "王阳明"}
MAX_CONVERSATION_BYTES = 1024 * 1024
MAX_CONVERSATION_MESSAGES = 200


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _history_cursor(version: str, before: int) -> str:
    return base64.urlsafe_b64encode(_json_bytes([version, before])).decode().rstrip("=")


def _history_offset(cursor: str, version: str, count: int) -> int:
    if not cursor:
        return count
    try:
        old, offset = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if old != version or type(offset) is not int or not 0 < offset <= count:
            raise ValueError()
        return offset
    except Exception:
        raise MobileError("conversation_page_changed") from None


def history_page(view: dict, scope: tuple[str, str], cursor: str) -> dict:
    """Serve a suffix of whole messages within the exact JSON UTF-8 budget."""
    version = hashlib.sha256(_json_bytes([scope, view])).hexdigest()
    messages = view["messages"]
    end = _history_offset(cursor, version, len(messages))
    page = dict(view, messages=[], history_version=version, next_cursor=None, history_has_more=False)
    for start in range(end - 1, max(-1, end - MAX_CONVERSATION_MESSAGES - 1), -1):
        candidate = dict(page, messages=messages[start:end], history_has_more=start > 0,
                         next_cursor=_history_cursor(version, start) if start else None)
        if len(_json_bytes(candidate)) > MAX_CONVERSATION_BYTES:
            if not page["messages"]:
                raise MobileError("conversation_message_too_large", 503)
            break
        page = candidate
    return page


class MobileChannel(LocalChannel):
    def __init__(self, runtime) -> None:
        super().__init__(runtime.store)
        self.runtime = runtime

    def send(self, delivery, text):
        conversation = self.store.read("conversation", delivery.conversation_id, Conversation)
        if conversation is None:
            raise MentorError("mobile_device_unavailable")
        self.runtime.service.policy.check_device(conversation)
        return super().send(delivery, text)


class MobileService:
    def __init__(self, store: MobileStore, runtime, memory_service) -> None:
        self.store, self.runtime = store, runtime
        self.memory = MobileMemory(memory_service)
        self.lock = threading.RLock()
        self.store.recover()
        self.runtime.service.policy.device_guard = self.check_conversation
        channel = self.runtime.service.policy.channel
        channel.adapters["mobile"] = MobileChannel(runtime)
        for app in runtime.store.list("application", "", Application):
            if app.platform == "mobile":
                channel.applications[app.id] = app

    def principal(self, device: dict) -> Principal:
        current = self.store.device(device["id"])
        allowed = set(self.runtime.user_tokens.values())
        principal = self.runtime.store.read("principal", current["principal"], Principal)
        if principal is None or principal.id not in allowed:
            raise MobileError("device_owner_unavailable", 401)
        return principal

    def authenticate(self, token: str) -> dict:
        device = self.store.authenticate(token)
        self.principal(device)
        return device

    def pair(self, code: str, name: str) -> dict:
        from riji_agent.mobile.store import digest
        with self.store.transaction() as db:
            row = db.execute("SELECT principal FROM pairings WHERE code_hash=?", (digest(code),)).fetchone()
        if row is None or row[0] not in set(self.runtime.user_tokens.values()):
            raise MobileError("pairing_invalid_or_expired", 401)
        return self.store.pair(code, name)

    def capabilities(self, device: dict) -> dict:
        owner = self.principal(device).legacy_owner_key
        return {"api_version": 1, "device": {"id": device["id"], "name": device["name"]},
                "capabilities": {"mentors": True, "memory": self.memory.service is not None,
                                 "privacy": self.memory._engine(owner, required=False) is not None,
                                 "roundtable": False, "workflows": False, "diary_upload": False},
                "mentors": [{"id": identifier, "name": name} for identifier, name in MENTORS.items()]}

    def operate(self, device: dict, kind: str, body: dict, effect: Callable[[], dict]) -> dict:
        with self.lock:
            self.principal(device)
            created, receipt = self.store.begin(device["id"], body["operation_id"], kind, body)
            if not created:
                return receipt
            try:
                result = effect()
            except MobileError as exc:
                return self.store.finish(device["id"], body["operation_id"], "failed", error=exc.code)
            except Exception:
                # Services can fail after a durable side effect. Never infer rollback.
                return self.store.finish(device["id"], body["operation_id"], "unknown", error="operation_outcome_unknown")
            return self.store.finish(device["id"], body["operation_id"], "completed", result=result)

    def _binding(self, device: dict, persona: str, *, create: bool = False) -> ChatBinding | None:
        self._persona(persona)
        principal = self.principal(device)
        identifier = self.store.binding(device["id"], persona)
        if identifier:
            binding = self.runtime.store.read("chat", identifier, ChatBinding)
            if binding is None or binding.principal_id != principal.id:
                raise MobileError("mobile_binding_unavailable", 503)
            return binding
        if not create:
            return None
        app = self.runtime.identity.register_application(Application(platform="mobile", tenant="personal-mobile-v1",
            external_id="riji-mobile-" + persona, persona_id=persona))
        self.runtime.service.policy.channel.applications[app.id] = app
        binding = ChatBinding(principal_id=principal.id, application_id=app.id,
                              external_chat_id="mobile-" + device["id"] + "-" + persona, chat_type="p2p")
        with self.runtime.store.transaction() as db:
            self.runtime.store.put(db, "chat", binding, principal.id)
        # Admission is durable before any runnable conversation is created.
        self.store.bind(device["id"], persona, binding.id)
        return binding

    @staticmethod
    def _persona(persona: str) -> None:
        if persona not in MENTORS:
            raise MobileError("mentor_not_found", 404)

    def check_conversation(self, conversation, binding: ChatBinding) -> None:
        try:
            device = self.store.binding_device(binding.id)
            principal = self.principal(device)
            app = self.runtime.store.read("application", binding.application_id, Application)
            if (principal.id != conversation.owner_id or app is None or app.platform != "mobile"
                    or conversation.kind != "private" or conversation.personas != (app.persona_id,)
                    or self.store.binding(device["id"], app.persona_id) != binding.id):
                raise MobileError("mobile_binding_unavailable")
        except MobileError:
            raise MentorError("mobile_device_unavailable") from None

    def send(self, device: dict, persona: str, body: dict) -> dict:
        if not body["text"].strip():
            raise MobileError("message_required", 422)
        binding = self._binding(device, persona, create=True)
        identifier = self.runtime.identity.current_conversation(binding, persona)
        if identifier is None:
            conversation = self.runtime.service.create(binding, body["text"], personas=(persona,))
            identifier = conversation.id
        else:
            conversation = self.runtime.service.get(identifier, device["principal"])
            self.check_conversation(conversation, binding)
            if conversation.status not in {"completed", "partial", "stopped", "interrupted"}:
                raise MobileError("mentor_busy")
            try:
                self.runtime.service.continue_mobile(Command(id="mobile:" + key(device["id"], body["operation_id"]),
                    principal_id=device["principal"], conversation_id=identifier,
                    expected_revision=conversation.input_revision, kind="supplement", text=body["text"]))
            except MentorError as exc:
                raise MobileError(exc.code) from None
        self.runtime.worker.wake()
        return {"conversation_id": identifier}

    def control(self, device: dict, persona: str, body: dict) -> dict:
        binding = self._binding(device, persona)
        identifier = self.runtime.identity.current_conversation(binding, persona) if binding else None
        if identifier is None:
            raise MobileError("conversation_not_found", 404)
        conversation = self.runtime.service.get(identifier, device["principal"])
        if body["action"] != "stop" and conversation.input_revision != body["expected_revision"]:
            raise MobileError("conversation_version_changed")
        self.check_conversation(conversation, binding)
        self.runtime.service.apply(Command(id="mobile:" + key(device["id"], body["operation_id"]),
            principal_id=device["principal"], conversation_id=identifier,
            expected_revision=body["expected_revision"], kind=body["action"]))
        self.runtime.worker.wake()
        return {"conversation_id": identifier}

    def conversation(self, device: dict, persona: str, cursor: str = "") -> dict:
        binding = self._binding(device, persona)
        identifier = self.runtime.identity.current_conversation(binding, persona) if binding else None
        if identifier is None:
            return history_page({"conversation_id": None, "status": "empty", "revision": 0,
                                 "messages": [], "notice": None}, (device["id"], persona), cursor)
        view = self.runtime.history.read(identifier, device["principal"])
        self.check_conversation(self.runtime.service.get(identifier, device["principal"]), binding)
        sent = {item.artifact_id for item in self.runtime.store.list("delivery", identifier, Delivery)
                if item.status == "sent" and item.application_id == binding.application_id
                and item.chat_id == binding.external_chat_id}
        messages = []
        for item in view["artifacts"]:
            if not ((item["kind"] == "user" and item["actor"] == "user")
                    or (item["kind"] == "followup" and item["actor"] == persona and item["id"] in sent)):
                continue
            refs = []
            for ref in item.get("dependencies", ()):
                source = self.runtime.store.read("source", ref, Source)
                if source is not None and source.kind == "memory":
                    refs.append(source.id)
            messages.append({"id": item["id"], "actor": item["actor"], "text": item.get("text", ""),
                             "created_at": item.get("created_at", 0), "source_ids": refs,
                             "unavailable": item.get("unavailable")})
        return history_page({"conversation_id": identifier, "status": view["conversation"]["status"],
                             "revision": view["conversation"]["input_revision"], "messages": messages,
                             "notice": view["notice"]}, (device["id"], persona), cursor)

    def revoke(self, device: dict) -> dict:
        self.store.revoke(device["id"])
        return {"revoked": True}
