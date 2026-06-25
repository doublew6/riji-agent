import json
from pathlib import Path

import pytest

from riji_agent.agent.tools import ToolRegistry
from riji_agent.journal.index import JournalIndex
from riji_agent.personas.registry import STANDARD_TOOLS, YANGMING_TOOLS, PersonaRegistry
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService
from riji_agent.yangming.models import CitationKind
from riji_agent.yangming.seed import load_seed
from riji_agent.yangming.store import YangmingKB


@pytest.fixture
def kb(tmp_path: Path) -> YangmingKB:
    store = YangmingKB(tmp_path / "ym.sqlite3")
    load_seed(store)
    yield store
    store.close()


# ------------------------------------------------------------------- knowledge base

def test_seed_is_searchable_with_provenance(kb: YangmingKB) -> None:
    hits = kb.search("心即理")
    assert hits, "expected a quote hit"
    hit = hits[0]
    assert hit.kind is CitationKind.QUOTE
    assert hit.ref.startswith("《传习录")
    assert hit.source and hit.version  # provenance recorded


def test_interpretation_chunks_are_labelled(kb: YangmingKB) -> None:
    hits = kb.search("致良知")
    kinds = {h.kind for h in hits}
    assert CitationKind.INTERPRETATION in kinds


# ------------------------------------------------------------------- persona

def test_wang_yangming_persona_uses_yangming_tools() -> None:
    persona = PersonaRegistry().get("wang_yangming")
    assert persona.uses_yangming is True
    assert "search_yangming" in persona.allowed_tools
    # does not impersonate the historical figure
    assert "不是王阳明本人" in persona.system_prompt or "不冒充" in persona.system_prompt


def test_other_personas_cannot_use_yangming_tool() -> None:
    for pid in ("gentle_reviewer", "blunt_coach", "future_self"):
        assert "search_yangming" not in PersonaRegistry().get(pid).allowed_tools


def test_all_personas_carry_high_risk_boundary() -> None:
    for persona in PersonaRegistry().all():
        assert "医疗" in persona.answer_boundaries and "投资" in persona.answer_boundaries


# ------------------------------------------------------------------- tool

@pytest.fixture
def registry(tmp_path: Path, kb: YangmingKB) -> ToolRegistry:
    index = JournalIndex(database_path=tmp_path / "idx.sqlite3", journal_root=tmp_path / "riji")
    return ToolRegistry(RetrievalService(index), yangming_kb=kb)


def _ctx() -> ToolContext:
    return ToolContext(request_id="r1", session_id="s", feishu_user_id="u1", persona_id="wang_yangming")


def test_search_yangming_separates_quotes_and_interpretations(registry: ToolRegistry) -> None:
    invocation = registry.invoke(_ctx(), "search_yangming", json.dumps({"query": "心即理"}))
    assert invocation.ok is True
    assert invocation.payload["corpus"] == "wang_yangming"
    assert "quotes" in invocation.payload and "interpretations" in invocation.payload
    assert invocation.payload["quotes"], "expected at least one cited quote"
    # kept distinct from the journal: no journal source ids leak through
    assert invocation.source_ids == ()


def test_quotes_carry_citation_and_version(registry: ToolRegistry) -> None:
    invocation = registry.invoke(_ctx(), "search_yangming", json.dumps({"query": "心即理"}))
    quote = invocation.payload["quotes"][0]
    assert quote["ref"].startswith("《传习录")
    assert quote["source"] and quote["version"]


def test_yangming_tool_gated_to_allowed_personas(registry: ToolRegistry) -> None:
    yangming_names = {t["function"]["name"] for t in registry.tool_specs(YANGMING_TOOLS)}
    standard_names = {t["function"]["name"] for t in registry.tool_specs(STANDARD_TOOLS)}
    assert "search_yangming" in yangming_names
    assert "search_yangming" not in standard_names


def test_yangming_tool_absent_without_kb(tmp_path: Path) -> None:
    index = JournalIndex(database_path=tmp_path / "i.sqlite3", journal_root=tmp_path / "riji")
    registry = ToolRegistry(RetrievalService(index))  # no KB
    invocation = registry.invoke(_ctx(), "search_yangming", json.dumps({"query": "心即理"}))
    assert invocation.error == "unknown_tool"
