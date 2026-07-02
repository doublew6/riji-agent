"""Models for safe self-evolution proposals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvolutionProposalStatus(str, Enum):
    AWAITING = "awaiting"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EvolutionProposal:
    proposal_id: str
    user_id: str
    session_id: str
    category: str
    title: str
    body: str
    status: EvolutionProposalStatus
    created_at: str
    updated_at: str
