"""Draft lifecycle: propose -> confirm -> atomic commit -> incremental index.

The model may only *propose* a draft (``create_draft``). Committing requires an
explicit, single-use confirmation bound to the draft, user and session, valid
for a limited time, so neither the model nor a duplicate message can write on
its own.
"""

from __future__ import annotations

import dataclasses
import logging
import json
import secrets
import uuid
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence

from riji_agent.drafts.errors import DraftError, DraftErrorCode
from riji_agent.drafts.confirmation import ConfirmationContext, PrivatePreviewScope, binding_payload, preview_hash
from riji_agent.drafts.models import (
    CommitResult,
    CommitVerification,
    Draft,
    DraftOperation,
    DraftPreview,
    DraftStatus,
)
from riji_agent.drafts.polish import polish_draft_content
from riji_agent.drafts.store import DraftStore
from riji_agent.drafts.writer import (
    WriteOutcome,
    WritePolicy,
    commit_operations,
    verify_committed_operations,
)
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.content import content_spans
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
            raise DraftError(
                DraftErrorCode.NO_OPERATIONS, "a draft needs at least one entry"
            )
        polished_operations = tuple(
            _polish_operation(operation) for operation in operations
        )
        now = self._now()
        target = target_date or now.date()
        polished_operations = tuple(dataclasses.replace(operation, provenance=dataclasses.replace(
            operation.provenance, saved_date=target.isoformat())) if operation.provenance else operation
            for operation in polished_operations)
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

    def render_preview(self, draft: Draft) -> str:
        """Redisplay an existing draft without creating another confirmation."""
        return self._render_preview(draft)

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

    def purge_uncommitted_draft(self, draft_id: str, *, user_id: str) -> bool:
        """Remove an unsaved handoff copy while preserving committed journal records."""
        draft = self._store.get(draft_id)
        if draft is None or draft.user_id != user_id or draft.status in {DraftStatus.COMMITTED, DraftStatus.COMMITTING}:
            return False
        self._store.save(dataclasses.replace(draft, operations=(), token="", status=DraftStatus.CANCELLED))
        return True

    def verify_latest_commit(
        self, *, user_id: str, session_id: str
    ) -> Optional[CommitVerification]:
        """Verify the latest committed draft against the current journal file."""
        draft = self._latest_committed_draft(user_id, session_id)
        if draft is None:
            return None
        path = self._journal_root / "daily" / f"{draft.target_date.isoformat()}.md"
        return CommitVerification(
            draft_id=draft.draft_id,
            source_id=draft.source_id,
            target_date=draft.target_date,
            verified=verify_committed_operations(path, draft.operations),
        )

    def ensure_latest_commit(
        self, *, user_id: str, session_id: str
    ) -> Optional[CommitVerification]:
        """Verify a confirmed draft and safely restore it if sync removed it."""
        draft = self._latest_committed_draft(user_id, session_id)
        if draft is None:
            return None
        path = self._journal_root / "daily" / f"{draft.target_date.isoformat()}.md"
        if verify_committed_operations(path, draft.operations):
            return self._commit_verification(draft, repaired=False)

        outcome = self._write_and_verify(draft)
        self._store.save(dataclasses.replace(draft, after_hash=outcome.after_hash))
        return self._commit_verification(draft, repaired=True)

    def commit_draft(
        self, draft_id: str, *, user_id: str, token: Optional[str] = None,
        confirmation: Optional[ConfirmationContext] = None,
        before_write: Optional[Callable[[], None]] = None,
    ) -> CommitResult:
        draft = self._store.get(draft_id)
        if draft is None:
            raise DraftError(DraftErrorCode.DRAFT_NOT_FOUND, "no such draft")
        if draft.status is not DraftStatus.AWAITING:
            raise DraftError(
                DraftErrorCode.NOT_AWAITING, "draft is no longer awaiting confirmation"
            )
        if draft.user_id != user_id:
            raise DraftError(
                DraftErrorCode.WRONG_USER, "confirmation must come from the same user"
            )
        if self._now() > datetime.fromisoformat(draft.expires_at):
            self._store.save(dataclasses.replace(draft, status=DraftStatus.EXPIRED))
            raise DraftError(
                DraftErrorCode.TOKEN_EXPIRED, "confirmation window has expired"
            )
        if not token or not secrets.compare_digest(token, draft.token):
            raise DraftError(
                DraftErrorCode.TOKEN_INVALID, "confirmation token does not match"
            )
        binding = self._validate_confirmation(draft, confirmation)

        # DB-level claim closes the check-then-act race: with multiple workers
        # several confirmations may all read AWAITING above, but only one wins
        # this atomic transition and proceeds to write. The losers see the row
        # already taken and get NOT_AWAITING, never a second append.
        if not self._store.claim_for_commit(draft_id, binding=binding):
            raise DraftError(
                DraftErrorCode.NOT_AWAITING, "draft is no longer awaiting confirmation"
            )

        try:
            outcome = self._write_and_verify(draft, before_write=before_write)
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
        return CommitResult(
            draft_id=draft.draft_id,
            source_id=outcome.source_id,
            target_date=draft.target_date,
            sections=outcome.sections,
            after_hash=outcome.after_hash,
            new_file=outcome.new_file,
        )

    def bind_preview(self, draft_id: str, scope: PrivatePreviewScope, display_event_id: str) -> str:
        draft = self._store.get(draft_id)
        if draft is None or draft.user_id != scope.user_id:
            raise DraftError(DraftErrorCode.DRAFT_NOT_FOUND, "no such draft")
        if scope.chat_type != "p2p" or not display_event_id:
            raise DraftError(DraftErrorCode.WRONG_SCOPE, "a verified private preview is required")
        if draft.status is not DraftStatus.AWAITING:
            raise DraftError(DraftErrorCode.NOT_AWAITING, "draft is no longer awaiting confirmation")
        payload = binding_payload(scope, draft)
        existing = self._store.preview_binding(draft_id)
        if existing and json.loads(existing)["scope"] != json.loads(payload)["scope"]:
            raise DraftError(DraftErrorCode.WRONG_SCOPE, "preview belongs to another private conversation")
        self._store.bind_preview(draft_id, payload, display_event_id)
        return preview_hash(draft)

    def has_preview_binding(self, draft_id: str) -> bool:
        return self._store.preview_binding(draft_id) is not None

    def _validate_confirmation(self, draft: Draft, confirmation: Optional[ConfirmationContext]) -> Optional[str]:
        existing = self._store.preview_binding(draft.draft_id)
        if any(operation.provenance is not None for operation in draft.operations) and (existing is None or confirmation is None):
            raise DraftError(DraftErrorCode.PREVIEW_REQUIRED, "AI discussion results require a verified private preview")
        if existing is None and confirmation is None:
            return None  # Direct local callers still require the explicit draft token.
        if confirmation is None or existing is None:
            raise DraftError(DraftErrorCode.PREVIEW_REQUIRED, "show the private preview again")
        expected = binding_payload(confirmation.scope, draft)
        if confirmation.scope.chat_type != "p2p" or expected != existing:
            raise DraftError(DraftErrorCode.WRONG_SCOPE, "confirmation scope does not match the preview")
        if (confirmation.draft_id != draft.draft_id or not confirmation.event_id
                or confirmation.preview_hash != preview_hash(draft)
                or not secrets.compare_digest(confirmation.token, draft.token)):
            raise DraftError(DraftErrorCode.TOKEN_INVALID, "confirmation does not match the displayed preview")
        return existing

    def _write_and_verify(self, draft: Draft, *, before_write: Optional[Callable[[], None]] = None) -> WriteOutcome:
        """Write, index, then verify again in case sync rolled back meanwhile."""
        outcome = self._write_and_index(draft, before_write=before_write)
        if verify_committed_operations(outcome.path, draft.operations):
            return outcome

        _LOG.warning("journal content rolled back during post-write indexing")
        outcome = self._write_and_index(draft, before_write=before_write)
        if verify_committed_operations(outcome.path, draft.operations):
            return outcome
        raise DraftError(
            DraftErrorCode.WRITE_VERIFICATION_FAILED,
            "journal write was repeatedly rolled back after indexing",
        )

    def _write_and_index(self, draft: Draft, *, before_write: Optional[Callable[[], None]] = None) -> WriteOutcome:
        outcome = commit_operations(
            self._journal_root, draft.target_date, draft.operations,
            policy=WritePolicy(before_replace=before_write),
        )
        try:
            self._index.update_note(outcome.path)
        except Exception:
            _LOG.warning("post-write incremental index update failed", exc_info=True)
        return outcome

    def _latest_committed_draft(
        self, user_id: str, session_id: str
    ) -> Optional[Draft]:
        draft = self._store.get_latest_committed_for_session(session_id)
        if draft is None or draft.user_id != user_id or draft.source_id is None:
            return None
        return draft

    @staticmethod
    def _commit_verification(
        draft: Draft, *, repaired: bool
    ) -> CommitVerification:
        assert draft.source_id is not None
        return CommitVerification(
            draft_id=draft.draft_id,
            source_id=draft.source_id,
            target_date=draft.target_date,
            verified=True,
            repaired=repaired,
        )

    @staticmethod
    def _render_preview(draft: Draft) -> str:
        lines = [f"草稿（{draft.target_date.isoformat()}）将追加："]
        for operation in draft.operations:
            lines.append(f"[{operation.section}]")
            lines.append(operation.journal_text if operation.provenance else f"  - {operation.content}")
        lines.append(
            f"回复「确认保存」写入（30 分钟内有效，仅一次）。"
            f"若期间切换了导师，改用「确认保存 {draft.draft_id}」。"
        )
        return "\n".join(lines)


def _polish_operation(operation: DraftOperation) -> DraftOperation:
    if operation.provenance is not None:
        return operation
    if any(span.content_type != "personal_journal" for span in content_spans(operation.content)):
        raise DraftError(DraftErrorCode.PREVIEW_REQUIRED, "AI discussion material requires the dedicated handoff flow")
    polished = polish_draft_content(operation.content)
    return dataclasses.replace(operation, content=polished or operation.content.strip())
