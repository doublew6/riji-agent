"""Private-channel preview bindings; only trusted gateways issue confirmations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from riji_agent.drafts.models import Draft


@dataclass(frozen=True)
class PrivatePreviewScope:
    user_id: str
    conversation_id: str
    platform: str
    app_binding_id: str
    chat_id: str
    chat_type: str = "p2p"


@dataclass(frozen=True)
class ConfirmationContext:
    scope: PrivatePreviewScope
    draft_id: str
    preview_hash: str
    event_id: str
    token: str


def preview_hash(draft: Draft) -> str:
    material = {
        "draft_id": draft.draft_id,
        "target_date": draft.target_date.isoformat(),
        "operations": [asdict(operation) for operation in draft.operations],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def binding_payload(scope: PrivatePreviewScope, draft: Draft) -> str:
    return json.dumps({"scope": asdict(scope), "preview_hash": preview_hash(draft)}, sort_keys=True)
