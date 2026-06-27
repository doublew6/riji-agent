"""First-class local chat: the real agent loop without Feishu or Hermes.

``riji-agent chat --question`` runs the same retrieval tools and model loop the
production gateway uses, but over loopback against the configured real vault and
model provider. It exists so a new user can validate their model key and journal
retrieval end-to-end before standing up the IM bridge.

The same boundaries hold as in the gateway: only the registered retrieval tools
are reachable, ``private: true`` notes are filtered by the retrieval service, and
tool-call metadata is audited. No IM transport, persona memory or draft-write
flow is involved; drafts/commits remain a Feishu-confirmed path.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from riji_agent.agent.loop import AgentResult, AgentRunner
from riji_agent.agent.tools import ToolRegistry
from riji_agent.audit.store import AuditStore
from riji_agent.config import Settings
from riji_agent.models.registry import build_model_provider
from riji_agent.models.types import LLMProvider
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService
from riji_agent.wiring import build_journal_index
from riji_agent.yangming.seed import load_seed
from riji_agent.yangming.store import YangmingKB

_LOCAL_USER_ID = "local"
_LOCAL_SESSION_ID = "local-cli"
_LOCAL_PERSONA_ID = "local"


def run_local_chat(
    settings: Settings,
    question: str,
    *,
    provider: Optional[LLMProvider] = None,
) -> str:
    """Answer ``question`` against the real vault via the local agent loop.

    ``provider`` lets a test inject a stub; otherwise the provider selected by
    ``settings.model_provider`` is built from the registry.
    """
    index = build_journal_index(settings)
    try:
        # Keep the index fresh for a one-shot run; incremental and bounded by the
        # configured per-file read timeout, so a cold vault file is skipped, not
        # hung on. Run `riji-agent index` first for large vaults.
        index.build_index(rebuild=False)

        retrieval = RetrievalService(index)
        yangming = YangmingKB(settings.data_dir / "yangming.sqlite3")
        if yangming.count() == 0:
            load_seed(yangming)
        registry = ToolRegistry(retrieval, yangming_kb=yangming)

        model = provider or build_model_provider(settings)
        runner = AgentRunner(model, registry)
        context = ToolContext(
            request_id=uuid4().hex,
            session_id=_LOCAL_SESSION_ID,
            feishu_user_id=_LOCAL_USER_ID,
            persona_id=_LOCAL_PERSONA_ID,
        )
        result = runner.run(context, question)

        audit = AuditStore(settings.data_dir / "audit.sqlite3")
        for entry in result.audit:
            audit.record(
                request_id=context.request_id,
                persona_id=context.persona_id,
                feishu_user_id=context.feishu_user_id,
                tool=entry.tool,
                ok=entry.ok,
                error=entry.error,
                source_ids=entry.source_ids,
            )
        return _format(result)
    finally:
        index.close()


def _format(result: AgentResult) -> str:
    lines = [result.answer.strip() or "(no answer)"]
    if result.sources:
        lines.append("")
        lines.append("Sources:")
        lines.extend(f"- [[{source_id}]]" for source_id in result.sources)
    return "\n".join(lines)
