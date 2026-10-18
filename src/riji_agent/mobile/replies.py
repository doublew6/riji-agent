"""Small delivery-backed cursors for local unread indicators."""

from __future__ import annotations

import sqlite3

from riji_agent.mentors.models import Artifact, Delivery
from riji_agent.mentors.store import MentorStore


def last_reply_id(store: MentorStore, conversation_id: str, chat_id: str, *,
                  db: sqlite3.Connection | None = None) -> str:
    if db is None:
        with store.transaction() as current:
            return last_reply_id(store, conversation_id, chat_id, db=current)
    deliveries = [item for item in store.records(db, 'delivery', conversation_id, Delivery)
                  if item.status == 'sent' and item.chat_id == chat_id]
    deliveries.sort(key=lambda item: (item.delivered_at, item.sequence), reverse=True)
    for delivery in deliveries:
        artifact = store.get(db, 'artifact', delivery.artifact_id, Artifact)
        if artifact is not None and artifact.kind != 'user':
            return artifact.id
    return ''
