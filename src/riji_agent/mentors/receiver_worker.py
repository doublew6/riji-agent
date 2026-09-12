"""Serial business forwarding and receipt sending outside the SDK callback."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

from riji_agent.mentors.models import Envelope
from riji_agent.mentors.receiver_spool import ReceiverSpool

LINK_GUIDE = "请先打开本地导师网页，在「连接飞书导师」中为这个应用生成绑定命令，再发到此私聊。不要发送网页访问令牌。"
UNKNOWN_GUIDE = "这条请求的处理结果暂时无法确认，请先在网页查看问题历史；不要重复发送保存或确认命令。"


class ReceiverWorker:
    def __init__(self, spool: ReceiverSpool, transport, channel) -> None:
        self.spool, self.transport, self.channel = spool, transport, channel
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name="mentor-receiver-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                if self.step():
                    continue
            except Exception:
                logging.getLogger("riji_agent.mentor_transport").warning("mentor receiver storage unavailable")
            self._wake.wait(timeout=1)

    def step(self) -> bool:
        item = self.spool.take()
        if item is None:
            return False
        identifier, message = item
        try:
            text, state = self._forward(message)
            if not text:
                self.spool.mark(identifier, state)
                return True
            self.spool.mark(identifier, "sending")
            result = self.channel.send(SimpleNamespace(application_id=self.spool.application_id,
                chat_id=message.external_chat_id, uuid=identifier[:32]), text)
            self.spool.mark(identifier, state if result.status == "sent" and result.message_id else "unknown",
                            result.message_id if result.status == "sent" else "")
        except Exception:
            # Timeout, malformed response or crash never triggers a blind retry.
            self.spool.mark(identifier, "unknown")
            logging.getLogger("riji_agent.mentor_transport").warning("mentor receiver outcome unknown")
        return True

    def _forward(self, message: Envelope) -> tuple[str, str]:
        if message.action_id == "unsupported_message":
            return "目前只支持文字消息，请用文字发送问题。", "rejected"
        response = self.transport.post("/api/mentors/v1/messages", json=message.model_dump())
        data = response.json()
        if response.status_code == 200:
            text = data.get("text", "")
            if not isinstance(text, str):
                raise ValueError("receiver_response_invalid")
            return text, "done"
        detail = data.get("detail")
        if response.status_code == 409 and detail == "identity_verification_required":
            return LINK_GUIDE, "rejected"
        if response.status_code >= 500 or detail == "incoming_reconciliation_required":
            return UNKNOWN_GUIDE, "unknown"
        if isinstance(detail, str) and detail.startswith("identity_link_"):
            return "绑定未完成或已失效，请在网页重新生成绑定命令。", "rejected"
        if detail == "feishu_roundtable_capability_not_verified":
            return "飞书圆桌正在配置中，目前可在网页使用圆桌讨论。", "rejected"
        return "暂未处理这条请求，请在私聊查看状态或使用 /帮助。", "rejected"
