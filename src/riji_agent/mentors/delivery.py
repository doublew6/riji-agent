"""Ordered outbox delivery with explicit uncertain outcomes and send-time guards."""

from __future__ import annotations

import time
from typing import Callable

from riji_agent.mentors.models import Artifact, Conversation, Delivery, Execution, MentorError, TransportResult
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import key


class OutboxDispatcher:
    def __init__(self, service: DiscussionService, now: Callable[[], float] = time.time) -> None:
        self.service, self.store, self.now = service, service.store, now

    def dispatch_one(self, conversation_id: str = "") -> bool:
        item = self._claim(conversation_id)
        if item is None:
            return self._complete_delivered(conversation_id)
        conversation = self.store.read("conversation", item.conversation_id, Conversation)
        execution = Execution(conversation_id=conversation.id, owner_id=conversation.owner_id,
                              run_id=conversation.run_id,
                              input_revision=item.input_revision, cancel_epoch=item.cancel_epoch,
                              lease_generation=conversation.lease_generation)
        try:
            self.service.policy.check(execution, require_lease=False)
            artifact = self.store.read("artifact", item.artifact_id, Artifact)
            if artifact is None:
                raise MentorError("artifact_unavailable")
            result = self.service.policy.channel.send(item, self._render(artifact))
        except MentorError:
            self._cancel(item)
            return True
        except Exception:
            result = TransportResult(status="unknown", error_code="delivery_unknown")
        self._record(item, result)
        return True

    def _claim(self, conversation_id: str) -> Delivery | None:
        with self.store.transaction() as db:
            rows = db.execute("SELECT value FROM mentor_records WHERE kind='delivery' ORDER BY rowid").fetchall()
            items = [Delivery.model_validate_json(row[0]) for row in rows]
            for item in items:
                if item.status != "pending" or (conversation_id and item.conversation_id != conversation_id):
                    continue
                earlier = [other for other in items if other.conversation_id == item.conversation_id
                           and other.sequence < item.sequence and other.status not in {"sent", "cancelled"}]
                conversation = self.store.get(db, "conversation", item.conversation_id, Conversation)
                if earlier or conversation.status not in {"queued", "running", "delivering", "partial"}:
                    continue
                item = item.model_copy(update={"status": "sending", "attempts": item.attempts + 1,
                                              "first_attempt_at": item.first_attempt_at or self.now()})
                self.store.put(db, "delivery", item, item.conversation_id)
                return item
        return None

    def _record(self, item: Delivery, result: TransportResult) -> None:
        if result.status == "sent" and not result.message_id:
            result = TransportResult(status="unknown", error_code="delivery_receipt_missing")
        with self.store.transaction() as db:
            current = self.store.get(db, "delivery", item.id, Delivery)
            if current is None:
                return
            status = {"sent": "sent", "not_sent": "failed", "unknown": "unknown"}[result.status]
            current = current.model_copy(update={"status": status, "provider_message_id": result.message_id})
            self.store.put(db, "delivery", current, item.conversation_id)
            if status == "sent":
                self.store.bind(db, "message_delivery", key(current.chat_id, result.message_id), current.id)
            else:
                conversation = self.store.get(db, "conversation", item.conversation_id, Conversation)
                if conversation.status not in {"deleted", "stopped"}:
                    conversation = conversation.model_copy(update={"status": "interrupted", "lease_until": 0})
                    self.store.put(db, "conversation", conversation, conversation.owner_id)
                    self.store.bind(db, "blocked", conversation.id, "delivery_" + status)
                    self.service.budgets.pause(db, conversation)

    def _cancel(self, item: Delivery) -> None:
        with self.store.transaction() as db:
            existing = self.store.get(db, "delivery", item.id, Delivery)
            if existing:
                self.store.put(db, "delivery", existing.model_copy(update={"status": "cancelled"}), item.conversation_id)

    def _complete_delivered(self, identifier: str) -> bool:
        with self.store.transaction() as db:
            rows = db.execute("SELECT value FROM mentor_records WHERE kind='conversation'").fetchall()
            for row in rows:
                conversation = Conversation.model_validate_json(row[0])
                if conversation.status != "delivering" or (identifier and identifier != conversation.id):
                    continue
                deliveries = self.store.records(db, "delivery", conversation.id, Delivery)
                if any(item.status not in {"sent", "cancelled"} for item in deliveries):
                    continue
                conversation = conversation.model_copy(update={"status": "completed", "room_status": "ended", "updated_at": self.now()})
                self.store.put(db, "conversation", conversation, conversation.owner_id)
                self.service.summaries.record_run(db, conversation)
                self.service.budgets.pause(db, conversation)
                return True
        return False

    @staticmethod
    def _render(artifact: Artifact) -> str:
        titles = {"opinion": "独立观点", "comparison": "共识与分歧", "debate": f"第 {artifact.round_index} 轮回应",
                  "synthesis": "综合建议", "followup": "继续沟通"}
        lines = [titles.get(artifact.kind, "进度"), artifact.text]
        if artifact.stance_change:
            lines.append("判断调整：" + artifact.stance_change)
        if artifact.uncertainties:
            lines.append("条件与未解决问题：\n" + "\n".join(artifact.uncertainties))
        if artifact.next_steps:
            lines.append("可选下一步：\n" + "\n".join(artifact.next_steps))
        return "\n\n".join(lines)

    def retry_known_unsent(self, delivery_id: str, principal_id: str) -> None:
        with self.store.transaction() as db:
            delivery = self.store.get(db, "delivery", delivery_id, Delivery)
            conversation = self.store.get(db, "conversation", delivery.conversation_id, Conversation) if delivery else None
            if conversation is None or conversation.owner_id != principal_id:
                raise MentorError("delivery_not_found")
            if delivery.status != "failed" or delivery.attempts >= 2:
                raise MentorError("delivery_reconciliation_required")
            self.store.put(db, "delivery", delivery.model_copy(update={"status": "pending"}), conversation.id)
            self.service.budgets.activate(db, conversation)
            self.store.put(db, "conversation", conversation.model_copy(update={"status": "queued"}), principal_id)
