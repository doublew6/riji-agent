"""Neutral failure notices sent only to a previously verified private origin."""

from riji_agent.mentors.models import ChatBinding, Conversation, Delivery
from riji_agent.mentors.store import key

REASONS = {
    "room_sealed": "群成员或可见范围有变化，原群已停止接收私人内容。",
    "verification_paused": "暂时无法完整核验群状态，讨论已暂停。",
    "source_revoked": "一项来源的版本或授权发生变化，讨论已暂停。",
    "model_authentication_failed": "模型登录或凭据验证失败，讨论已暂停。请检查模型配置后再继续。",
    "model_permission_denied": "模型服务拒绝访问，讨论已暂停。请检查模型使用权限。",
    "model_rate_limited": "模型服务限制了请求，讨论已暂停。请稍后查看状态，不会自动重发。",
    "model_quota_exhausted": "模型账户额度不足，讨论已暂停。不会自动切换其他模型服务。",
    "model_timeout": "模型请求超时，讨论已暂停。结果是否已生成尚不确定，需核对后再继续。",
    "model_connection_failed": "连接模型服务失败，讨论已暂停。请检查连接并核对本次状态。",
    "model_transport_failed": "模型连接中断，讨论已暂停。结果尚不确定，需核对后再继续。",
    "model_server_failed": "模型服务返回错误，讨论已暂停。结果尚不确定，需核对后再继续。",
    "model_output_invalid": "模型返回的内容格式无效，讨论已暂停。请核对本次状态后再继续。",
    "model_refused": "模型未提供本次回答，讨论已暂停。可以调整问题后再继续。",
    "feishu_roundtable_capability_not_verified": "飞书私人群能力尚未通过验证，当前不会建群或发送私人资料。",
}


def dispatch_notice(service) -> bool:
    store = service.store
    with store.transaction() as db:
        row = db.execute("SELECT key,value FROM mentor_keys WHERE kind='private_notice' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return False
        conversation = store.get(db, "conversation", row["key"], Conversation)
        origin = store.lookup(db, "origin", row["key"])
        binding = store.get(db, "chat", origin, ChatBinding) if origin else None
        valid = (conversation is not None and conversation.status != "deleted" and binding is not None
                 and binding.principal_id == conversation.owner_id and binding.chat_type == "p2p")
        db.execute("DELETE FROM mentor_keys WHERE kind='private_notice' AND key=?", (row["key"],))
        if not valid:
            return True
        operation = key(conversation.id, str(conversation.cancel_epoch), row["value"])
        if store.lookup(db, "notice_attempt", operation):
            return True
        store.bind(db, "notice_attempt", operation, "attempted")
        delivery = Delivery(conversation_id=conversation.id, sequence=0, application_id=binding.application_id,
            chat_id=binding.external_chat_id, artifact_id="", input_revision=conversation.input_revision,
            cancel_epoch=conversation.cancel_epoch)
    text = REASONS.get(row["value"], "私人讨论已暂停，请在已验证的私聊或本地记录中查看状态。")
    try:
        result = service.policy.channel.send(delivery, text)
        status = result.status
    except Exception:
        status = "unknown"
    with store.transaction() as db:
        store.bind(db, "notice_attempt", operation, status)
    return True
