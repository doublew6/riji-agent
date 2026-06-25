"""Draft lifecycle: propose -> confirm -> atomic commit -> incremental index.

The model may only *propose* a draft (``create_draft``). Committing requires an
explicit, single-use confirmation bound to the draft, user and session, valid
for a limited time, so neither the model nor a duplicate message can write on
its own.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.models import (
    CommitResult,
    Draft,
    DraftOperation,
    DraftPreview,
    DraftStatus,
)
from riji_agent.drafts.store import DraftStore
from riji_agent.drafts.writer import commit_operations
from riji_agent.journal.index import JournalIndex


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class DraftService:
    def __init__(
        self,
        store: DraftStore,
        journal_root: Path,
        index: JournalIndex,
        *,
        ttl_minutes: int = 30,
        now: Callable[[], datetime] = _default_now,
    ) -> None:
        self._store = store
        self._journal_root = Path(journal_root)
        self._index = index
        self._ttl = timedelta(minutes=ttl_minutes)
        self._now = now

    def create_draft(
        self,
        *,
        user_id: str,
        session_id: str,
        persona_id: str,
        operations: Sequence[DraftOperation],
        target_date: Optional[Date] = None,
    ) -> DraftPreview:
        if not operations:
            raise DraftError(DraftErrorCode.NO_OPERATIONS, "a draft needs at least one entry")
        now = self._now()
        target = target_date or now.date()
        draft = Draft(
            draft_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            persona_id=persona_id,
            target_date=target,
            operations=tuple(operations),
            token=uuid.uuid4().hex,
            status=DraftStatus.AWAITING,
            created_at=now.isoformat(),
            expires_at=(now + self._ttl).isoformat(),
        )
        self._store.save(draft)
        return DraftPreview(
            draft_id=draft.draft_id,
            target_date=target,
            operations=draft.operations,
            token=draft.token,
            expires_at=draft.expires_at,
            preview_text=self._render_preview(draft),
        )

    def get_latest_awaiting_for_session(self, session_id: str) -> Optional[Draft]:
        return self._store.get_latest_awaiting_for_session(session_id)

    def commit_draft(
        self, draft_id: str, *, user_id: str, token: Optional[str] = None
    ) -> CommitResult:
        draft = self._store.get(draft_id)
        if draft is None:
            raise DraftError(DraftErrorCode.DRAFT_NOT_FOUND, "no such draft")
        if draft.status is not DraftStatus.AWAITING:
            raise DraftError(DraftErrorCode.NOT_AWAITING, "draft is no longer awaiting confirmation")
        if draft.user_id != user_id:
            raise DraftError(DraftErrorCode.WRONG_USER, "confirmation must come from the same user")
        if self._now() > datetime.fromisoformat(draft.expires_at):
            self._store.save(dataclasses.replace(draft, status=DraftStatus.EXPIRED))
            raise DraftError(DraftErrorCode.TOKEN_EXPIRED, "confirmation window has expired")
        if token is not None and token != draft.token:
            raise DraftError(DraftErrorCode.TOKEN_INVALID, "confirmation token does not match")

        # May raise SECTION_NOT_FOUND / TEMPLATE_NOT_FOUND before any file is touched;
        # the draft is left awaiting so the user can adjust.
        outcome = commit_operations(self._journal_root, draft.target_date, draft.operations)

        self._store.save(
            dataclasses.replace(
                draft,
                status=DraftStatus.COMMITTED,
                source_id=outcome.source_id,
                after_hash=outcome.after_hash,
            )
        )
        self._index.update_note(outcome.path)
        return CommitResult(
            draft_id=draft.draft_id,
            source_id=outcome.source_id,
            target_date=draft.target_date,
            sections=outcome.sections,
            after_hash=outcome.after_hash,
            new_file=outcome.new_file,
        )

    @staticmethod
    def _render_preview(draft: Draft) -> str:
        lines = [f"草稿（{draft.target_date.isoformat()}）将追加："]
        for operation in draft.operations:
            lines.append(f"[{operation.section}]")
            lines.append(f"  - {operation.content}")
        lines.append("回复「确认保存」以写入（30 分钟内有效，仅一次）。")
        return "\n".join(lines)
