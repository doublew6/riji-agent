from pathlib import Path
from typing import List, Sequence

import pytest

from riji_agent.journal.embedding import HashingEmbeddingProvider
from riji_agent.journal.index import JournalIndex
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService

# Stub embedder: maps "AI-ish" texts to one direction, everything else to another,
# so a paraphrase query with no lexical overlap still lands near AI notes.
_AI_CONCEPT = ("机器学习", "人工智能", "AI", "深度学习", "神经网络")


class StubEmbedder:
    @property
    def dim(self) -> int:
        return 2

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [[1.0, 0.0] if any(k in t for k in _AI_CONCEPT) else [0.0, 1.0] for t in texts]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "riji"
    _write(root / "daily" / "2026-06-10.md", "---\ndate: 2026-06-10\n---\n# 2026-06-10\n今天深入研究了机器学习的最新进展。\n")
    _write(root / "daily" / "2026-06-11.md", "---\ndate: 2026-06-11\n---\n# 2026-06-11\n周末去爬山，天气很好。\n")
    return root


# ----------------------------------------------------------- embedding provider

def test_hashing_provider_is_deterministic_and_normalised() -> None:
    p = HashingEmbeddingProvider(dim=64)
    v1 = p.embed(["机器学习的进展"])[0]
    v2 = p.embed(["机器学习的进展"])[0]
    assert v1 == v2  # stable across calls (and restarts)
    assert len(v1) == 64
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-9  # L2-normalised


# ------------------------------------------------------------------ hybrid search

def test_semantic_recalls_a_note_fts_misses(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    index = JournalIndex(tmp_path / "i.sqlite3", root, embedder=StubEmbedder())
    index.build_index()

    # "人工智能" never appears literally in the note, so FTS alone finds nothing...
    fts_only = JournalIndex(tmp_path / "i2.sqlite3", root)  # no embedder
    fts_only.build_index()
    assert fts_only.search("人工智能") == []

    # ...but hybrid search recalls the machine-learning note semantically.
    hits = index.search("人工智能")
    assert "riji/daily/2026-06-10" in {h.source_id for h in hits}


def test_keyword_match_still_works_in_hybrid(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    index = JournalIndex(tmp_path / "i.sqlite3", root, embedder=StubEmbedder())
    index.build_index()
    hits = index.search("机器学习")
    assert "riji/daily/2026-06-10" in {h.source_id for h in hits}


def test_fallback_is_pure_fts_without_embedder(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    index = JournalIndex(tmp_path / "i.sqlite3", root)  # default: no semantic
    index.build_index()
    assert index.search("人工智能") == []  # behaviour unchanged from before #17


def test_private_excluded_from_semantic_results(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _write(root / "daily" / "2026-06-12.md", "---\ndate: 2026-06-12\nprivate: true\n---\n# 2026-06-12\n关于深度学习的私密笔记。\n")
    index = JournalIndex(tmp_path / "i.sqlite3", root, embedder=StubEmbedder())
    index.build_index()

    ids = {h.source_id for h in index.search("人工智能", include_private=False)}
    assert "riji/daily/2026-06-12" not in ids  # private never surfaced, even semantically
    assert "riji/daily/2026-06-10" in ids


def test_embeddings_are_incremental(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    index = JournalIndex(tmp_path / "i.sqlite3", root, embedder=StubEmbedder())
    index.build_index()
    assert "riji/daily/2026-06-10" in {h.source_id for h in index.search("人工智能")}

    # rewrite the note to a non-AI topic; only this note's embedding is recomputed
    (root / "daily" / "2026-06-10.md").write_text(
        "---\ndate: 2026-06-10\n---\n# 2026-06-10\n今天整理了一份烹饪食谱。\n", encoding="utf-8"
    )
    index.update_note(root / "daily" / "2026-06-10.md")
    assert "riji/daily/2026-06-10" not in {h.source_id for h in index.search("人工智能")}


def test_delete_removes_embedding(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    index = JournalIndex(tmp_path / "i.sqlite3", root, embedder=StubEmbedder())
    index.build_index()
    index.remove_source("riji/daily/2026-06-10")
    assert "riji/daily/2026-06-10" not in {h.source_id for h in index.search("人工智能")}


# --------------------------------------------- privacy contract through retrieval

def test_retrieval_service_keeps_privacy_with_hybrid(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _write(root / "daily" / "2026-06-12.md", "---\ndate: 2026-06-12\nprivate: true\n---\n# 2026-06-12\n关于神经网络的私密想法。\n")
    index = JournalIndex(tmp_path / "i.sqlite3", root, embedder=StubEmbedder())
    index.build_index()
    service = RetrievalService(index)
    ctx = ToolContext(request_id="r", session_id="s", feishu_user_id="u", persona_id="p")

    result = service.search_journal(ctx, "人工智能")
    ids = {i.source_id for i in result.items}
    assert "riji/daily/2026-06-10" in ids
    assert "riji/daily/2026-06-12" not in ids  # private excluded through the hybrid path
