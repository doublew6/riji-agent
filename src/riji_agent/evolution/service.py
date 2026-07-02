"""Safe proposal queue for Hermes self-evolution."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from typing import Callable

from riji_agent.evolution.models import EvolutionProposal, EvolutionProposalStatus
from riji_agent.evolution.store import EvolutionProposalStore
from riji_agent.timezone import local_journal_timezone


class EvolutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _default_now() -> datetime:
    return datetime.now(local_journal_timezone())


class EvolutionService:
    """Create and approve local improvement proposals.

    This is deliberately not an execution engine. It stores only sanitized,
    high-level proposals and never runs terminal/browser/file-system actions.
    """

    def __init__(
        self,
        store: EvolutionProposalStore,
        *,
        now: Callable[[], datetime] = _default_now,
    ) -> None:
        self._store = store
        self._now = now

    def create_proposal(self, *, user_id: str, session_id: str, request_text: str) -> EvolutionProposal:
        now = self._now().isoformat()
        category, title, body = _proposal_content(request_text)
        proposal = EvolutionProposal(
            proposal_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            category=category,
            title=title,
            body=body,
            status=EvolutionProposalStatus.AWAITING,
            created_at=now,
            updated_at=now,
        )
        self._store.save(proposal)
        return proposal

    def approve_latest(self, *, user_id: str, session_id: str) -> EvolutionProposal:
        proposal = self._store.latest_awaiting_for_session(session_id)
        if proposal is None:
            raise EvolutionError("no_pending_evolution_proposal")
        if proposal.user_id != user_id:
            raise EvolutionError("proposal_not_found")
        return self._set_status(proposal, EvolutionProposalStatus.APPROVED)

    def reject_latest(self, *, user_id: str, session_id: str) -> EvolutionProposal:
        proposal = self._store.latest_awaiting_for_session(session_id)
        if proposal is None:
            raise EvolutionError("no_pending_evolution_proposal")
        if proposal.user_id != user_id:
            raise EvolutionError("proposal_not_found")
        return self._set_status(proposal, EvolutionProposalStatus.REJECTED)

    def render_preview(self, proposal: EvolutionProposal) -> str:
        return (
            "已生成一条安全改进提案：\n"
            f"类别：{proposal.category}\n"
            f"标题：{proposal.title}\n"
            f"{proposal.body}\n\n"
            "回复「确认改进」只会标记为已批准；具体代码、权限或自动化变更仍需单独实现和审查。\n"
            "回复「拒绝改进」可丢弃这条提案。"
        )

    def _set_status(
        self, proposal: EvolutionProposal, status: EvolutionProposalStatus
    ) -> EvolutionProposal:
        updated = dataclasses.replace(
            proposal,
            status=status,
            updated_at=self._now().isoformat(),
        )
        self._store.save(updated)
        return updated


def _proposal_content(request_text: str) -> tuple[str, str, str]:
    compact = request_text.lower()
    if any(word in compact for word in ("skill", "技能", "沉淀")):
        return (
            "skill",
            "沉淀可复用 skill 草案",
            "建议从本次需求中抽取可复用步骤，形成待审查 skill 草案。",
        )
    if any(word in compact for word in ("自动化", "cron", "定时", "每周", "每天")):
        return (
            "automation",
            "创建受控自动化草案",
            "建议生成自动化草案，先 dry-run 和隐私扫描，再由用户确认启用。",
        )
    if any(word in compact for word in ("issue", "问题", "失败", "不好用")):
        return (
            "issue",
            "整理改进 issue 草案",
            "建议将重复失败或体验问题整理为 GitHub issue 草案，发布前移除私密内容。",
        )
    return (
        "review",
        "分析系统改进建议",
        "建议基于审计元数据和失败计数生成改进计划，不使用日记正文或聊天原文。",
    )
