"""Validated business contracts without transport or orchestration SDK types."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool


def new_id() -> str:
    return str(uuid4())


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MentorError(Exception):
    """Safe, stable errors never containing user text or external responses."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Account(Record):
    platform: str
    tenant: str
    subject: str


class Principal(Record):
    id: str = Field(default_factory=new_id)
    account: Account
    legacy_owner_key: str


class Application(Record):
    id: str = Field(default_factory=new_id)
    platform: str
    tenant: str
    external_id: str
    persona_id: str
    role: Literal["mentor", "host"] = "mentor"


class Envelope(Record):
    delivery_id: str = Field(min_length=1, max_length=300)
    message_id: str = Field(min_length=1, max_length=300)
    external_user_id: str = Field(min_length=1, max_length=300)
    subject: str = Field(default="", max_length=300)
    external_chat_id: str = Field(min_length=1, max_length=300)
    chat_type: Literal["p2p", "group"]
    sender_kind: Literal["user", "bot"] = "user"
    text: str = Field(max_length=10000)
    reply_to: str = ""
    action_id: str = ""
    mentioned_external_ids: tuple[str, ...] = ()
    mentioned_names: tuple[str, ...] = ()


class ChatBinding(Record):
    id: str = Field(default_factory=new_id)
    principal_id: str
    application_id: str
    external_chat_id: str
    chat_type: Literal["p2p", "group"]


class Source(Record):
    id: str
    owner_id: str
    version: str
    text: str = Field(max_length=4000)
    kind: Literal["question", "memory", "journal", "knowledge", "shared_excerpt"]
    allowed_personas: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    origin: str = ""
    content_kind: Literal["unknown", "user_statement", "user_plan", "user_feedback", "ai_discussion", "mixed"] = "unknown"


class ComparisonStance(Record):
    artifact_id: str = Field(min_length=1, max_length=128)
    quote: str = Field(min_length=1, max_length=1200)


class ComparisonFinding(Record):
    decision: str = Field(min_length=1, max_length=400)
    shared_condition: str = Field(min_length=1, max_length=600)
    relationship: Literal["compatible", "complementary", "conflict", "uncertain"]
    stances: tuple[ComparisonStance, ComparisonStance]
    rationale: str = Field(min_length=1, max_length=800)


class Artifact(Record):
    id: str = Field(default_factory=new_id)
    conversation_id: str
    actor: str
    kind: Literal["user", "opinion", "comparison", "debate", "synthesis", "followup", "status"]
    input_revision: int
    run_id: str = ""
    origin_room_id: str = ""
    origin_kind: Literal["user_statement", "user_plan", "user_feedback", "ai_discussion"] = "ai_discussion"
    round_index: int = 0
    text: str = Field(max_length=12000)
    claims: tuple[str, ...] = ()
    responds_to: tuple[str, ...] = ()
    stance_change: str = ""
    source_refs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    debate_needed: bool | None = None
    comparison_findings: tuple[ComparisonFinding, ...] = Field(default=(), max_length=2)
    created_at: float


class Conversation(Record):
    id: str = Field(default_factory=new_id)
    owner_id: str
    kind: Literal["private", "roundtable"]
    personas: tuple[str, ...]
    question: str = Field(min_length=1, max_length=10000)
    mode: Literal["private", "reference", "debate"]
    input_revision: int = 1
    state_revision: int = 1
    cancel_epoch: int = 0
    lease_generation: int = 0
    lease_until: float = 0
    status: str = "queued"
    room_status: str = "preparing"
    room_id: str = ""
    grant_id: str = ""
    rounds: int = 2
    debate_started: bool = False
    source_scope: Literal["personal", "group_only"] = "personal"
    source_application_id: str = ""
    source_platform: str = ""
    source_tenant: str = ""
    source_ids: tuple[str, ...] = ()
    run_id: str = Field(default_factory=new_id)
    run_kind: str = "initial"
    run_personas: tuple[str, ...] = ()
    run_number: int = 1
    summary_id: str = ""
    summary_version: int = 0
    summary_status: Literal["current", "pending"] = "pending"
    correction_version: int = 0
    reanalyze: bool = False
    suspended_run_id: str = ""
    followup_actor: str = ""
    summarize_requested: bool = False
    created_at: float
    updated_at: float


class RoomSnapshot(Record):
    room_id: str
    human_subjects: tuple[str, ...]
    application_ids: tuple[str, ...]
    private: bool
    complete: bool
    management_restricted: bool
    history_restricted: bool
    configuration_version: str
    continuity_verified: bool = True


class AudienceGrant(Record):
    id: str = Field(default_factory=new_id)
    conversation_id: str
    owner_id: str
    input_revision: int
    snapshot: RoomSnapshot
    source_versions: dict[str, str]
    active: bool = True


class Execution(Record):
    conversation_id: str
    # Older internal cursors omit this; every newly claimed execution binds it.
    owner_id: str = ""
    run_id: str
    input_revision: int
    cancel_epoch: int
    lease_generation: int


class Delivery(Record):
    id: str = Field(default_factory=new_id)
    conversation_id: str
    sequence: int
    application_id: str
    chat_id: str
    artifact_id: str
    input_revision: int
    cancel_epoch: int
    status: str = "pending"
    provider_message_id: str = ""
    first_attempt_at: float = 0
    attempts: int = 0
    uuid: str = Field(default_factory=new_id)


class Command(Record):
    id: str = Field(min_length=1, max_length=300)
    principal_id: str
    conversation_id: str
    kind: str
    expected_revision: int
    text: str = Field(default="", max_length=10000)
    actor: str = ""
    preview_hash: str = ""
    personas: tuple[str, ...] = ()
    mode: Literal["reference", "debate"] = "reference"
    rounds: int = Field(default=2, ge=1, le=2)
    supersedes: tuple[str, ...] = ()
    statement_kind: Literal["user_statement", "user_plan", "user_feedback"] = "user_statement"
    replace_background: bool = False
    reanalyze: bool = False


class Receipt(Record):
    command_id: str
    conversation_id: str
    status: str
    input_revision: int
    deduplicated: bool = False


class SummaryItem(Record):
    kind: Literal["user_statement", "user_plan", "user_feedback", "ai_advice", "unresolved"]
    text: str = Field(max_length=12000)
    artifact_ids: tuple[str, ...]
    source_refs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    occurred_at: float


class WorkingSummary(Record):
    id: str = Field(default_factory=new_id)
    conversation_id: str
    version: int
    input_revision: int
    correction_version: int
    covered_artifact_ids: tuple[str, ...]
    items: tuple[SummaryItem, ...]
    status: Literal["current", "pending"] = "current"
    pending_artifact_ids: tuple[str, ...] = ()
    created_at: float


class DiscussionRun(Record):
    id: str
    conversation_id: str
    number: int
    kind: str
    mode: str
    personas: tuple[str, ...]
    summary_version: int
    input_revision: int
    status: str
    reanalyze: bool = False
    created_at: float
    updated_at: float


class GenerationRequest(Record):
    conversation: Conversation
    actor: str
    stage: str
    round_index: int
    background: tuple[Source, ...]
    previous: tuple[Artifact, ...]
    execution: Execution
    repair_hint: str = ""
    working_summary: WorkingSummary | None = None


class Generation(Record):
    text: str = Field(min_length=1, max_length=12000)
    claims: tuple[str, ...] = ()
    responds_to: tuple[str, ...] = ()
    stance_change: str = ""
    source_refs: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    debate_needed: StrictBool | None = None
    comparison_findings: tuple[ComparisonFinding, ...] = Field(default=(), max_length=2)


class TransportResult(Record):
    status: Literal["sent", "not_sent", "unknown"]
    message_id: str = ""
    error_code: str = ""
