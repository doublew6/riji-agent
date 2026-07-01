"""Draft lifecycle: propose -> confirm -> atomic commit -> incremental index.

The model may only *propose* a draft (``create_draft``). Committing requires an
explicit, single-use confirmation bound to the draft, user and session, valid
for a limited time, so neither the model nor a duplicate message can write on
its own.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import date as Date
from datetime import datetime, timedelta
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
from riji_agent.drafts.polish import polish_draft_content
from riji_agent.drafts.writer import commit_operations
from riji_agent.journal.index import JournalIndex
from riji_agent.timezone import local_journal_timezone


def _default_now() -> datetime:
    return datetime.now(local_journal_timezone())


_LOG = logging.getLogger("riji_agent.drafts.service")


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
        polished_operations = tuple(_polish_operation(operation) for operation in operations)
        now = self._now()
        target = target_date or now.date()
        draft = Draft(
            draft_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            persona_id=persona_id,
            target_date=target,
            operations=polished_operations,
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

    def get_latest_for_session(self, session_id: str) -> Optional[Draft]:
        return self._store.get_latest_for_session(session_id)

    def get_draft(self, draft_id: str) -> Optional[Draft]:
        """Fetch a draft by its id, regardless of session.

        Lets the gateway confirm by explicit ``draft_id`` even when the user
        switched personas between proposal and confirmation. Ownership/expiry/
        status are still enforced in :meth:`commit_draft`.
        """
        return self._store.get(draft_id)

    def cancel_draft(self, draft_id: str, *, user_id: Optional[str] = None) -> bool:
        draft = self._store.get(draft_id)
        if draft is None:
            return False
        if user_id is not None and draft.user_id != user_id:
            return False
        if draft.status is not DraftStatus.AWAITING:
            return False
        self._store.save(dataclasses.replace(draft, status=DraftStatus.CANCELLED))
        return True

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

        # DB-level claim closes the check-then-act race: with multiple workers
        # several confirmations may all read AWAITING above, but only one wins
        # this atomic transition and proceeds to write. The losers see the row
        # already taken and get NOT_AWAITING, never a second append.
        if not self._store.claim_for_commit(draft_id):
            raise DraftError(
                DraftErrorCode.NOT_AWAITING, "draft is no longer awaiting confirmation"
            )

        try:
            # May raise SECTION_NOT_FOUND / TEMPLATE_NOT_FOUND before any file is
            # touched (os.replace is atomic and the post-write code cannot raise),
            # so a failure means nothing was written.
            outcome = commit_operations(self._journal_root, draft.target_date, draft.operations)
        except Exception:
            # Release the claim so the user can fix the issue and retry.
            self._store.save(dataclasses.replace(draft, status=DraftStatus.AWAITING))
            raise

        self._store.save(
            dataclasses.replace(
                draft,
                status=DraftStatus.COMMITTED,
                source_id=outcome.source_id,
                after_hash=outcome.after_hash,
            )
        )
        try:
            self._index.update_note(outcome.path)
        except Exception:
            _LOG.warning("post-commit incremental index update failed", exc_info=True)
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
        lines = [f"草稿（{draft.target_date.isoformat()} {draft.target_date:%A}）将追加："]
        for operation in draft.operations:
            lines.append(f"[{operation.section}]")
            lines.append(f"  - {operation.content}")
        lines.append(
            f"回复「确认保存」写入（30 分钟内有效，仅一次）。"
            f"若期间切换了导师，改用「确认保存 {draft.draft_id}」。"
        )
        return "\n".join(lines)


def _polish_operation(operation: DraftOperation) -> DraftOperation:
    polished = polish_draft_content(operation.content)
    return dataclasses.replace(operation, content=polished or operation.content.strip())
