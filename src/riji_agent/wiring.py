"""Production wiring: assemble every local module into one HermesGateway.

This is the single place that turns configured settings into a runnable
service. It owns no policy of its own — it only constructs the components the
tests already exercise (index, retrieval, tools, memory, drafts, audit, the
Yangming KB and the configured model provider) and hands them to the gateway.

Boundaries preserved here: the journal vault is opened read-only, the API key
only ever reaches the model provider, and all local state lives in SQLite
files under the configured data directory.
"""

from __future__ import annotations

from typing import Optional

from riji_agent.agent.hermes import HermesAgentRuntime
from riji_agent.agent.tools import ToolRegistry
from riji_agent.audit.store import AuditStore
from riji_agent.config import Settings
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.responder import AgentResponder
from riji_agent.journal.embedding import embedder_from_settings
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.scheduler import IndexScheduler
from riji_agent.memory.store import MemoryStore
from riji_agent.models.registry import build_model_provider
from riji_agent.models.types import LLMProvider
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.service import RetrievalService
from riji_agent.yangming.seed import load_seed
from riji_agent.yangming.store import YangmingKB


def build_journal_index(settings: Settings) -> JournalIndex:
    """Open the read-only journal index with optional local embeddings.

    No indexing is performed here; the caller drives it (CLI prewarm or the
    background :class:`IndexScheduler`) so startup is never blocked unboundedly.
    """
    settings.ensure_data_directory()
    return JournalIndex(
        database_path=settings.resolved_database_path,
        journal_root=settings.journal_root,
        embedder=embedder_from_settings(settings),
        file_read_timeout=settings.index_file_timeout_seconds,
    )


def build_production_gateway(
    settings: Settings,
    *,
    provider: Optional[LLMProvider] = None,
    index: Optional[JournalIndex] = None,
) -> HermesAgentRuntime:
    """Construct the fully wired gateway for ``settings``.

    ``provider`` lets a test or an alternate local model stand in for the
    DeepSeek client. ``index`` lets the caller share an index it already owns
    (e.g. one attached to the :class:`IndexScheduler`); otherwise one is opened.
    The returned gateway carries the ``index_scheduler`` it is wired to.
    """
    settings.ensure_data_directory()
    data_dir = settings.data_dir

    journal_index = index or build_journal_index(settings)
    retrieval = RetrievalService(journal_index)

    # Draft writes go through confirm + atomic append and re-index on commit.
    draft_service = DraftService(
        DraftStore(data_dir / "drafts.sqlite3"), settings.journal_root, journal_index
    )

    # Wang Yangming KB is a separate corpus; seed it once on first start.
    yangming = YangmingKB(data_dir / "yangming.sqlite3")
    if yangming.count() == 0:
        load_seed(yangming)

    registry = ToolRegistry(retrieval, draft_service=draft_service, yangming_kb=yangming)

    # Dispatch on settings.model_provider via the registry; DeepSeek is the
    # default, but no provider is hardcoded here.
    model = provider or build_model_provider(settings)

    audit = AuditStore(data_dir / "audit.sqlite3")
    responder = AgentResponder(model, registry, audit_store=audit)

    gateway = HermesAgentRuntime(
        hermes_secret=settings.hermes_shared_secret.get_secret_value(),
        allowed_user_ids=settings.allowed_feishu_user_ids,
        registry=PersonaRegistry(),
        store=MemoryStore(data_dir / "memory.sqlite3"),
        events=EventLog(data_dir / "events.sqlite3"),
        responder=responder,
        draft_service=draft_service,
    )
    # Carry the scheduler so the app can prewarm/refresh and report status.
    gateway.index_scheduler = IndexScheduler(
        journal_index,
        interval_seconds=settings.index_interval_seconds,
        enabled=settings.index_schedule_enabled,
    )
    return gateway
