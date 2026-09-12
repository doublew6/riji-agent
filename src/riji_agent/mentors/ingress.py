"""Deterministic user controls; normalized events never let bots trigger bots."""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import nullcontext
from datetime import date

from riji_agent.mentors.history import DiscussionHistory
from riji_agent.mentors.group_dialogue import GroupDialogue, is_stop_intent
from riji_agent.mentors.models import Command, Delivery, Envelope, MentorError
from riji_agent.mentors.store import key

HELP = ("直接发送问题即可与当前导师沟通。\n/新问题 内容；/历史\n"
        "固定导师：/切换 问题ID；Riji：/切换问题 问题ID\n"
        "Riji：/圆桌参考 导师ID,导师ID | 问题；/圆桌辩论 导师ID,导师ID | 问题\n"
        "/停止 问题ID；/继续 问题ID；/先总结 问题ID；/开始辩论 问题ID\n"
        "/补充 问题ID | 内容；/追问 问题ID 导师ID | 内容；/保存讨论 问题ID\n"
        "日记导师私聊：/修改转交 转交ID | 新正文；/转交日期 转交ID YYYY-MM-DD")


class DiscussionIngress:
    def __init__(self, service, worker, history: DiscussionHistory, handoffs=None) -> None:
        self.service, self.store, self.worker, self.history, self.handoffs = service, service.store, worker, history, handoffs
        self._lock = threading.RLock()
        self.identity_links = None
        self.groups = GroupDialogue(service, history, handoffs)

    def receive_owner(self, principal_id: str, identifier: str, operation_id: str, text: str) -> dict:
        """The authenticated local input has its own durable, revision-independent receipt."""
        conversation = self.service.get(identifier, principal_id)
        self.service.check_input_scope(conversation, "stop" if is_stop_intent(text) else "supplement")
        event_key = key(principal_id, identifier, operation_id)
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        with nullcontext() if is_stop_intent(text) else self._lock:
            with self.store.transaction() as db:
                receipt = self.store.lookup(db, "owner_message", event_key)
                if receipt:
                    saved = json.loads(receipt)
                    if saved["fingerprint"] != fingerprint:
                        raise MentorError("event_conflict")
                    if "result" not in saved:
                        raise MentorError("incoming_reconciliation_required")
                    return {**saved["result"], "deduplicated": True}
                self.store.bind(db, "owner_message", event_key, json.dumps({"fingerprint": fingerprint}))
            conversation = self.service.get(identifier, principal_id)
            result = self.groups.receive(conversation, "owner:" + hashlib.sha256(event_key.encode()).hexdigest(), text)
            with self.store.transaction() as db:
                self.store.bind(db, "owner_message", event_key,
                                json.dumps({"fingerprint": fingerprint, "result": result}, ensure_ascii=False))
            self.worker.wake()
            return result

    def receive(self, application_id: str, message: Envelope) -> dict:
        # Do not persist pairing commands in incoming receipts or conversation data.
        if message.text.strip().startswith("/绑定"):
            if self.identity_links is None:
                raise MentorError("identity_link_unavailable")
            return self.identity_links.claim(application_id, message)
        principal, app, binding = self.service.identity.resolve(application_id, message)
        if message.chat_type == "group":
            self._group(app, binding, message)
        event_key = key(application_id, message.external_chat_id, message.message_id, message.action_id)
        fingerprint = hashlib.sha256(message.model_dump_json(exclude={"delivery_id"}).encode()).hexdigest()
        urgent = (message.text.lstrip().startswith(("/停止 ", "/删除 ", "/先总结 "))
                  or (message.chat_type == "group" and is_stop_intent(message.text)))
        with nullcontext() if urgent else self._lock:
            with self.store.transaction() as db:
                receipt = self.store.lookup(db, "incoming", event_key)
                if receipt:
                    saved = json.loads(receipt)
                    if saved["fingerprint"] != fingerprint:
                        raise MentorError("event_conflict")
                    if "conversation_id" not in saved:
                        raise MentorError("incoming_reconciliation_required")
                    return {"status": "duplicate", "conversation_id": saved.get("conversation_id", "")}
                # An interrupted handler is never blindly replayed on restart.
                self.store.bind(db, "incoming", event_key, json.dumps({"fingerprint": fingerprint}))
            result = self._handle(app, binding, message, hashlib.sha256(event_key.encode()).hexdigest())
            with self.store.transaction() as db:
                self.store.bind(db, "incoming", event_key, json.dumps({"fingerprint": fingerprint,
                                "conversation_id": result.get("conversation_id", "")}))
            self.worker.wake()
            return result

    def _group(self, app, binding, message) -> None:
        if app.role != "host":
            raise MentorError("host_is_only_group_receiver")
        with self.store.transaction() as db:
            identifier = self.store.lookup(db, "room", key(app.platform, app.tenant, message.external_chat_id))
        if not identifier:
            raise MentorError("unmanaged_group")
        conversation = self.service.get(identifier, binding.principal_id)
        # Stop remains available even when membership checks fail.
        if not (message.text.strip().startswith(("/停止", "/删除")) or is_stop_intent(message.text)):
            self.service.policy._check_audience(conversation)

    def _target(self, identifier, app, binding):
        conversation = self.service.get(identifier, binding.principal_id)
        if binding.chat_type == "group" and conversation.room_id != binding.external_chat_id:
            raise MentorError("wrong_discussion_room")
        if app.role == "mentor" and (conversation.kind != "private" or conversation.personas != (app.persona_id,)):
            raise MentorError("fixed_persona_required")
        return conversation

    def _handle(self, app, binding, message, command_id) -> dict:
        text = message.text.strip()
        if binding.chat_type == "group" and not text.startswith("/"):
            with self.store.transaction() as db:
                identifier = self.store.lookup(db, "room", key(app.platform, app.tenant, binding.external_chat_id))
            conversation = self._target(identifier, app, binding)
            return self.groups.receive(conversation, command_id, text,
                                       mentioned_names=message.mentioned_names, group_binding=binding)
        if text in {"/帮助", "/help", "/讨论帮助"}:
            return {"text": HELP}
        if text == "/历史":
            if binding.chat_type != "p2p":
                raise MentorError("private_history_required")
            entries = self.history.list(binding.principal_id)
            entries = [item for item in entries if app.role == "host" or item["personas"] == (app.persona_id,)]
            return {"text": "\n".join(item["id"] + " · " + item["question"][:80] for item in entries[-20:]) or "还没有问题记录。"}
        if text.startswith("/圆桌"):
            return self._roundtable(app, binding, text)
        if text.startswith("/分享 "):
            if binding.chat_type != "p2p" or app.role != "host":
                raise MentorError("private_preparation_required")
            _, identifier, fingerprint = text.split()
            conversation = self._target(identifier, app, binding)
            receipt = self.service.apply(Command(id=command_id, principal_id=binding.principal_id,
                conversation_id=identifier, expected_revision=conversation.input_revision, kind="share", preview_hash=fingerprint))
            return {"text": "已确认分享范围，正在核验私人圆桌。", **receipt.model_dump()}
        if text.startswith(("/接收转交 ", "/确认转交 ")):
            action, identifier = text.split(maxsplit=1)
            if self.handoffs is None:
                raise MentorError("handoff_unavailable")
            method = self.handoffs.preview if action == "/接收转交" else self.handoffs.confirm
            return {"text": method(identifier, binding, message.message_id)}
        if text.startswith(("/修改转交 ", "/转交日期 ")):
            if self.handoffs is None:
                raise MentorError("handoff_unavailable")
            head, separator, content = text.partition("|")
            parts = head.split()
            if parts[0] == "/修改转交":
                if len(parts) != 2 or not separator or not content.strip():
                    raise MentorError("handoff_revision_required")
                return {"text": self.handoffs.revise(parts[1], binding, message.message_id,
                                                       text=content.strip())}
            if len(parts) != 3 or separator:
                raise MentorError("handoff_date_invalid")
            try:
                target_date = date.fromisoformat(parts[2])
            except ValueError:
                raise MentorError("handoff_date_invalid") from None
            return {"text": self.handoffs.revise(parts[1], binding, message.message_id,
                                                   target_date=target_date)}
        if text.startswith("/保存讨论 "):
            return self._handoff(app, binding, text.split(maxsplit=1)[1], command_id)
        commands = {"/停止": "stop", "/删除": "delete", "/继续": "resume", "/先总结": "summarize",
                    "/开始辩论": "debate", "/补充": "supplement", "/追问": "followup", "/切换": "select",
                    "/切换问题": "select"}
        action = text.split(maxsplit=1)[0] if text else ""
        if action in commands:
            return self._command(commands[action], app, binding, text, command_id)
        if binding.chat_type != "p2p" or app.role != "mentor":
            return {"text": HELP}
        return self._private(app, binding, message, command_id)

    def _roundtable(self, app, binding, text) -> dict:
        head, separator, question = text.partition("|")
        action, _, names = head.strip().partition(" ")
        if not separator or action not in {"/圆桌参考", "/圆桌辩论"}:
            return {"text": HELP}
        personas = tuple(item.strip() for item in names.split(",") if item.strip())
        conversation = self.service.create(binding, question.strip(), personas=personas,
                                          mode="reference" if action == "/圆桌参考" else "debate")
        preview = self.service.share_preview(conversation.id, binding.principal_id)
        lines = ["请确认本次私人圆桌的分享范围：", question.strip(), "导师：" + "、".join(personas)]
        lines.extend(source["text"] for source in preview["sources"])
        lines.append("不加入其他导师的私聊历史。确认请发送：\n/分享 " + conversation.id + " " + preview["preview_hash"])
        return {"conversation_id": conversation.id, "text": "\n\n".join(lines)}

    def _command(self, kind, app, binding, text, command_id) -> dict:
        head, _, content = text.partition("|")
        parts = head.split()
        if len(parts) < 2:
            return {"text": HELP}
        conversation = self._target(parts[1], app, binding)
        if kind == "select":
            self.service.identity.select_conversation(binding, app.persona_id, conversation.id)
            return {"conversation_id": conversation.id, "text": "已切换到这个问题。"}
        if kind == "delete":
            result = self.history.delete(conversation.id, binding.principal_id, command_id)
        else:
            result = self.service.apply(Command(id=command_id, principal_id=binding.principal_id,
                conversation_id=conversation.id, kind=kind, expected_revision=conversation.input_revision,
                text=content.strip(), actor=parts[2] if len(parts) == 3 else ""), group_binding=binding).model_dump()
        return {"text": "已处理：" + result["status"], **result}

    def _private(self, app, binding, message, command_id) -> dict:
        text = message.text.strip()
        identifier = self.service.identity.current_conversation(binding, app.persona_id)
        if message.reply_to:
            with self.store.transaction() as db:
                delivery_id = self.store.lookup(db, "message_delivery", key(binding.external_chat_id, message.reply_to))
            delivery = self.store.read("delivery", delivery_id, Delivery) if delivery_id else None
            if delivery is None or delivery.application_id != app.id:
                raise MentorError("reply_target_unavailable")
            identifier = delivery.conversation_id
        if text.startswith("/新问题 ") or identifier is None:
            question = text.removeprefix("/新问题 ").strip()
            conversation = self.service.create(binding, question, personas=(app.persona_id,))
            return {"status": "queued", "conversation_id": conversation.id, "text": "已收到新问题。"}
        conversation = self._target(identifier, app, binding)
        receipt = self.service.apply(Command(id=command_id, principal_id=binding.principal_id,
            conversation_id=identifier, expected_revision=conversation.input_revision, kind="supplement", text=text))
        return {"text": "已收到。", **receipt.model_dump()}

    def _handoff(self, app, binding, identifier, operation_id) -> dict:
        conversation = self._target(identifier, app, binding)
        view = self.history.read(conversation.id, binding.principal_id)
        selected = [item["id"] for item in view["artifacts"] if item["kind"] in {"synthesis", "comparison", "followup"}
                    and "unavailable" not in item and not item.get("superseded")][-1:]
        if self.handoffs is None:
            raise MentorError("handoff_unavailable")
        handoff = self.handoffs.create(conversation.id, binding.principal_id, tuple(selected), operation_id=operation_id)
        return {"text": "请到 Riji 私聊发送以下命令，查看草稿后再确认保存：\n/接收转交 " + handoff.id}
