"""Audit event model.

An audit event records *metadata only*: which tool ran, which source ids it
surfaced, the outcome and the time. It never stores API keys, request bodies or
full note content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class AuditEvent:
    request_id: str
    persona_id: str
    feishu_user_id: str
    tool: str
    ok: bool
    error: Optional[str]
    source_ids: Tuple[str, ...]
    created_at: str
