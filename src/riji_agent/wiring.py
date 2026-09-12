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

from typing import Optional, Tuple

from riji_agent.agent.hermes import HermesAgentRuntime
from riji_agent.agent.tools import ToolRegistry
from riji_agent.audit.store import AuditStore
from riji_agent.calendar.providers import FeishuCalendarProvider
from riji_agent.calendar.service import CalendarService
from riji_agent.calendar.store import CalendarDraftStore
from riji_agent.config import Settings
from riji_agent.drafts.service import DraftService
from riji_agent.drafts.store import DraftStore
from riji_agent.evolution.service import EvolutionService
from riji_agent.evolution.store import EvolutionProposalStore
from riji_agent.hermes.events import EventLog
from riji_agent.hermes.responder import AgentResponder
from riji_agent.journal.embedding import embedder_from_settings
from riji_agent.journal.index import JournalIndex
from riji_agent.journal.scheduler import IndexScheduler
from riji_agent.memory.store import MemoryStore
from riji_agent.memory.backend import LongTermMemoryBackend
from riji_agent.memory.capture import DeepSeekMemoryExtractor
from riji_agent.memory.mem0 import Mem0Client
from riji_agent.memory.operations import MemoryOperationsStore
from riji_agent.memory.service import CaptureProcessor, MemoryService
from riji_agent.memory.organization import MemoryOrganizer
from riji_agent.memory.snapshot import MemorySnapshotWriter
from riji_agent.memory.worker import MemoryWorker
from riji_agent.memory.journal_backend import JournalEvidenceBackend
from riji_agent.memory.journal_engine import JournalMemoryEngine
from riji_agent.memory.journal_store import JournalMemoryStore
from riji_agent.memory.journal_types import JournalMemoryPolicy
from riji_agent.models.registry import (
    build_memory_model_provider,
    build_model_provider,
    model_processing_target,
)
from riji_agent.models.types import LLMProvider
from riji_agent.personas.registry import PersonaRegistry
from riji_agent.retrieval.service import RetrievalService
from riji_agent.voice.service import (
    MacOSSayVoiceReplyService,
    MeloTTSVoiceReplyService,
    VoxCPMVoiceReplyService,
    VoiceReplyService,
)
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


def build_voice_reply_service(settings: Settings) -> Optional[VoiceReplyService]:
    if settings.feishu_voice_reply_mode == "off":
        return None
    output_dir = settings.tts_output_dir or (settings.data_dir / "voice")
    if settings.tts_provider == "macos_say":
        return MacOSSayVoiceReplyService(
            output_dir,
            voice=settings.tts_voice,
            max_chars=settings.tts_max_chars,
        )
    if settings.tts_provider == "melotts":
        return MeloTTSVoiceReplyService(
            output_dir,
            language=settings.tts_language,
            speaker=settings.tts_voice,
            device=settings.tts_device,
            speed=settings.tts_speed,
            max_chars=settings.tts_max_chars,
        )
    if settings.tts_provider == "voxcpm":
        return VoxCPMVoiceReplyService(
            output_dir,
            model_name=settings.tts_model,
            voice=settings.tts_voice,
            cfg_value=settings.tts_cfg_value,
            inference_timesteps=settings.tts_inference_timesteps,
            max_chars=settings.tts_max_chars,
        )
    return None


def build_calendar_service(
    settings: Settings,
    *,
    journal_index: JournalIndex,
) -> Optional[CalendarService]:
    if settings.calendar_provider == "off":
        return None
    assert settings.feishu_app_secret is not None
    provider = FeishuCalendarProvider(
        app_id=settings.feishu_app_id or "",
        app_secret=settings.feishu_app_secret,
        calendar_id=settings.feishu_calendar_id,
        base_url=settings.feishu_open_base_url,
    )
    return CalendarService(
        CalendarDraftStore(settings.data_dir / "calendar.sqlite3"),
        provider,
        journal_root=settings.journal_root,
        index=journal_index,
    )


def build_memory_runtime(
    settings: Settings,
    *,
    personas: Optional[PersonaRegistry] = None,
    backend: Optional[LongTermMemoryBackend] = None,
    extractor_provider: Optional[LLMProvider] = None,
) -> Tuple[Optional[MemoryService], Optional[MemoryWorker]]:
    if settings.memory_provider != "mem0":
        return None, None
    registry = personas or PersonaRegistry()
    memory_backend = backend or Mem0Client(
        settings.mem0_base_url,
        settings.mem0_api_key.get_secret_value(),  # type: ignore[union-attr]
    )
    operations_path = settings.data_dir / "memory-operations.sqlite3"
    extractor_model = extractor_provider or build_memory_model_provider(settings)
    journal = build_journal_memory(settings, memory_backend, extractor_model, personas=registry)
    if journal is not None:
        memory_backend = JournalEvidenceBackend(memory_backend, journal)
    snapshot = _build_memory_snapshot(settings, memory_backend, registry)
    service = MemoryService(
        memory_backend,
        MemoryOperationsStore(operations_path),
        snapshot,
        context_max_chars=settings.memory_context_max_chars,
        auto_capture=settings.memory_auto_capture,
    )
    processor = CaptureProcessor(
        memory_backend,
        MemoryOperationsStore(operations_path),
        DeepSeekMemoryExtractor(extractor_model),
        snapshot,
    )
    organizer = MemoryOrganizer(memory_backend, service.operations.organization, extractor_model)
    service.journal = journal
    if journal is not None:
        journal.on_change = lambda: _journal_changed(service, journal.policy.user_id)
    return service, MemoryWorker(processor, organizer=organizer, journal=journal)


def build_journal_memory(settings: Settings, backend: LongTermMemoryBackend,
                         provider: LLMProvider, *,
                         personas: Optional[PersonaRegistry] = None) -> Optional[JournalMemoryEngine]:
    path = settings.data_dir / "journal-memory.sqlite3"
    if not settings.journal_memory_enabled and not path.is_file():
        return None
    store = JournalMemoryStore(path)
    owner = settings.journal_memory_user_id or store.get_control("user_id")
    extraction = model_processing_target(settings, "memory")
    recall = model_processing_target(settings, "chat")
    policy = JournalMemoryPolicy(
        settings.journal_root, owner,
        tuple(value.strip() for value in settings.journal_memory_sections.split(",") if value.strip()),
        date_from=settings.journal_memory_date_from, date_to=settings.journal_memory_date_to,
        segment_chars=settings.journal_memory_segment_chars, source_chars=settings.journal_memory_source_chars,
        daily_chars=settings.journal_memory_daily_chars, scan_seconds=settings.journal_memory_scan_seconds,
        initialization_unlimited=settings.journal_memory_initialization_unlimited,
        read_timeout=settings.index_file_timeout_seconds or 2.0,
        enabled=settings.journal_memory_enabled,
        extraction_destination=extraction.destination,
        extraction_provider=extraction.provider,
        extraction_model=extraction.model,
        recall_destination=recall.destination,
        recall_provider=recall.provider,
        recall_model=recall.model,
        mentors=(personas or PersonaRegistry()).ids(),
    )
    return JournalMemoryEngine(policy, store, backend, provider)


def _journal_changed(service: MemoryService, user_id: str) -> None:
    service.request_snapshot()
    service.operations.organization.request(user_id)
    if service.journal and (service.journal.initialization_status()["active"]
            or service.journal.store.get_control("organization_budget_recheck_requested") == "1"):
        service.operations.organization.wake_daily_budget(user_id)
        service.journal.store.set_control("organization_budget_recheck_requested", "0")


def _build_memory_snapshot(
    settings: Settings,
    backend: LongTermMemoryBackend,
    personas: PersonaRegistry,
) -> Optional[MemorySnapshotWriter]:
    if not settings.memory_snapshot_enabled:
        return None
    return MemorySnapshotWriter(
        backend,
        settings.memory_snapshot_path,  # type: ignore[arg-type]
        user_ids=settings.allowed_feishu_user_ids,
        persona_names={item.persona_id: item.name for item in personas.all()},
    )


def build_production_gateway(
    settings: Settings,
    *,
    provider: Optional[LLMProvider] = None,
    index: Optional[JournalIndex] = None,
    memory_backend: Optional[LongTermMemoryBackend] = None,
    memory_extractor_provider: Optional[LLMProvider] = None,
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

    memory_store = MemoryStore(data_dir / "memory.sqlite3")
    registry = ToolRegistry(
        retrieval, draft_service=draft_service, yangming_kb=yangming, memory_store=memory_store
    )

    # Dispatch on settings.model_provider via the registry; DeepSeek is the
    # default, but no provider is hardcoded here.
    model = provider or build_model_provider(settings)

    audit = AuditStore(data_dir / "audit.sqlite3")
    responder = AgentResponder(
        model,
        registry,
        audit_store=audit,
        runtime_trace_policy_path=settings.runtime_trace_policy_path,
    )

    personas = PersonaRegistry()
    memory_service, memory_worker = build_memory_runtime(
        settings,
        personas=personas,
        backend=memory_backend,
        extractor_provider=memory_extractor_provider,
    )

    gateway = HermesAgentRuntime(
        hermes_secret=settings.hermes_shared_secret.get_secret_value(),
        allowed_user_ids=settings.allowed_feishu_user_ids,
        registry=personas,
        store=memory_store,
        events=EventLog(data_dir / "events.sqlite3"),
        responder=responder,
        draft_service=draft_service,
        calendar_service=build_calendar_service(settings, journal_index=journal_index),
        evolution_service=EvolutionService(EvolutionProposalStore(data_dir / "evolution.sqlite3")),
        voice_reply_service=build_voice_reply_service(settings),
        memory_service=memory_service,
        memory_worker=memory_worker,
    )
    # Carry the scheduler so the app can prewarm/refresh and report status.
    gateway.index_scheduler = IndexScheduler(
        journal_index,
        interval_seconds=settings.index_interval_seconds,
        enabled=settings.index_schedule_enabled,
    )
    if settings.mentors_enabled:
        from riji_agent.mentors.runtime import build_runtime
        gateway.mentor_runtime = build_runtime(settings, model=model, memory_service=memory_service,
                                               drafts=draft_service)
    return gateway
