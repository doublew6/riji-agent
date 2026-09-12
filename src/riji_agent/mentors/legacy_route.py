"""Explicit discussion commands on the existing, authenticated Riji gateway."""

from __future__ import annotations

import hashlib

from riji_agent.hermes.models import GatewayReply
from riji_agent.im.models import IncomingChatMessage
from riji_agent.mentors.models import Envelope, MentorError
from riji_agent.mentors.receiver_worker import LINK_GUIDE, UNKNOWN_GUIDE

# /切换 remains the original persona command; discussion selection is explicit.
DISCUSSION_COMMANDS = frozenset({"/绑定", "/讨论帮助", "/历史", "/圆桌参考", "/圆桌辩论",
    "/分享", "/接收转交", "/确认转交", "/保存讨论", "/停止", "/删除", "/继续",
    "/先总结", "/开始辩论", "/补充", "/追问", "/切换问题", "/修改转交", "/转交日期"})


def route_host_message(runtime, message: IncomingChatMessage) -> GatewayReply | None:
    """Caller must have passed Hermes secret, private-chat and user allowlist gates."""
    parts = message.text.strip().split(maxsplit=1)
    if not parts or parts[0] not in DISCUSSION_COMMANDS:
        return None
    identifier = hashlib.sha256(message.event_id.encode()).hexdigest()
    host = getattr(runtime, "legacy_host", None)
    if host is None:
        return GatewayReply(identifier, "host", "飞书讨论入口尚未配置，请先在网页使用导师功能。")
    try:
        envelope = Envelope(delivery_id=message.event_id, message_id=message.event_id,
            external_user_id=message.user_id, external_chat_id=message.chat_id,
            chat_type=message.chat_type, text=message.text)
        result = runtime.ingress.receive(host.id, envelope)
        text = result.get("text") or "这条消息已处理，请在网页查看问题状态。"
        return GatewayReply(identifier, "host", text, deduplicated=result.get("status") == "duplicate")
    except MentorError as exc:
        text = _safe_error(exc.code)
    except Exception:
        text = UNKNOWN_GUIDE
    return GatewayReply(identifier, "host", text)


def _safe_error(code: str) -> str:
    if code == "identity_verification_required":
        return LINK_GUIDE
    if code == "feishu_roundtable_capability_not_verified":
        return "飞书圆桌尚未通过群权限验收，请先在网页使用圆桌讨论。"
    if code.startswith("identity_link_"):
        return "绑定未完成或已失效，请在网页重新生成绑定命令。"
    if code == "incoming_reconciliation_required":
        return UNKNOWN_GUIDE
    return "这条讨论命令未完成，请在网页查看状态或发送 /讨论帮助。"
