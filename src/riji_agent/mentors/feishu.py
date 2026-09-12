"""Feishu SDK edge. Group disclosure stays disabled until platform probes pass."""

from __future__ import annotations

import json
import logging

from riji_agent.mentors.feishu_group import FeishuRoomEvidence, FeishuRoomInspector
from riji_agent.mentors.models import Envelope, MentorError, RoomSnapshot, TransportResult


class SafeSDKLogs(logging.Filter):
    """The SDK formats external errors and connection URLs; keep only a safe code."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = "feishu_sdk_transport_event", ()
        record.exc_info, record.exc_text, record.stack_info = None, None, None
        return True


def configure_sdk_logging() -> None:
    logger = logging.getLogger("Lark")
    if not any(isinstance(item, SafeSDKLogs) for item in logger.filters):
        logger.addFilter(SafeSDKLogs())


def build_client(app_id: str, secret: str):
    import lark_oapi as lark
    configure_sdk_logging()
    return lark.Client.builder().app_id(app_id).app_secret(secret).timeout(15).log_level(lark.LogLevel.ERROR).build()


def normalize(event, application, *, allow_unsupported: bool = False) -> Envelope:
    data = event.event
    sender, message = data.sender, data.message
    if (sender.sender_type != "user" or sender.tenant_key != application.tenant
            or getattr(event.header, "app_id", None) != application.external_id
            or sender.sender_id is None or not sender.sender_id.open_id):
        raise MentorError("feishu_identity_unverified")
    unsupported = message.message_type != "text"
    if unsupported and not allow_unsupported:
        raise MentorError("feishu_text_required")
    content = {"text": ""} if unsupported else json.loads(message.content)
    text = content.get("text") if isinstance(content, dict) else None
    if not isinstance(text, str):
        raise MentorError("feishu_text_required")
    text, mention_ids, mention_names = _mentions(message, text)
    return Envelope(delivery_id=event.header.event_id, message_id=message.message_id,
        external_user_id=sender.sender_id.open_id, subject=sender.sender_id.user_id or "",
        external_chat_id=message.chat_id, chat_type=message.chat_type, text=text.strip(),
        reply_to=message.parent_id or "", action_id="unsupported_message" if unsupported else "",
        mentioned_external_ids=mention_ids, mentioned_names=mention_names)


def _mentions(message, text: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    mentions = message.mentions or ()
    if len(mentions) > 10:
        raise MentorError("feishu_mentions_invalid")
    identifiers, names = [], []
    for mention in mentions:
        identifier = getattr(getattr(mention, "id", None), "open_id", None) or ""
        name = getattr(mention, "name", None) or ""
        marker = getattr(mention, "key", None)
        if (not isinstance(identifier, str) or len(identifier) > 300
                or not isinstance(name, str) or len(name) > 100
                or not isinstance(marker, str) or not marker or len(marker) > 100):
            raise MentorError("feishu_mentions_invalid")
        # Names are untrusted routing hints. They never authenticate a sender.
        identifiers.append(identifier)
        names.append(name)
        text = text.replace(marker, "")
    return text, tuple(identifiers), tuple(names)


def normalize_host_group(event, application) -> Envelope:
    """Separate group edge for a verified host transport, never the DM gateway."""
    if application.persona_id != "host":
        raise MentorError("host_is_only_group_receiver")
    message = normalize(event, application, allow_unsupported=True)
    if message.chat_type != "group":
        raise MentorError("feishu_group_message_required")
    return message


class FeishuChannel:
    def __init__(self, applications: dict, clients: dict, *, member_resolver=None) -> None:
        self.applications, self.clients = applications, clients
        self.member_resolver = member_resolver

    def create_room(self, principal, applications, operation_id):
        # No credential flag or manual boolean can substitute for FG-01..05.
        # SDK 1.7.3 does not expose a provable new-member history restriction.
        raise MentorError("feishu_roundtable_capability_not_verified")

    def inspect_room(self, room_id) -> RoomSnapshot:
        return self.inspect_room_evidence(room_id).snapshot

    def inspect_room_evidence(self, room_id: str) -> FeishuRoomEvidence:
        return FeishuRoomInspector(self.applications, self.clients,
                                   member_resolver=self.member_resolver).inspect(room_id)

    def send(self, delivery, text) -> TransportResult:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        body = CreateMessageRequestBody.builder().receive_id(delivery.chat_id).msg_type("text").content(
            json.dumps({"text": text}, ensure_ascii=False)).uuid(delivery.uuid).build()
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        try:
            response = self.clients[delivery.application_id].im.v1.message.create(request)
        except Exception:
            return TransportResult(status="unknown", error_code="feishu_delivery_unknown")
        if not response.success():
            # Error classifications need real FG-04 evidence; default to uncertain.
            return TransportResult(status="unknown", error_code="feishu_delivery_unconfirmed")
        message_id = response.data.message_id if response.data is not None else ""
        return TransportResult(status="sent" if message_id else "unknown", message_id=message_id or "")
