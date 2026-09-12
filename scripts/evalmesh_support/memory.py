"""Synthetic memory component and local FTS AgentRunner evaluations.

No settings, environment files, production vaults or network backends are loaded.
Gold annotations are deliberately outside the target's input contract. Passing
the machine invariants does not mean that semantic quality has been reviewed.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from riji_agent.agent.loop import AgentLimits, AgentRunner
from riji_agent.agent.tools import ToolInvocation, ToolRegistry
from riji_agent.journal.index import JournalIndex
from riji_agent.memory.journal_extract import JournalMemoryExtractor
from riji_agent.memory.journal_types import JournalEvidence, fingerprint
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.models.types import LLMProvider
from riji_agent.retrieval.models import ToolContext
from riji_agent.retrieval.service import RetrievalService

_CITATION = re.compile(r"\[\[(riji/[^\]\n|#]+)(?:[|#][^\]\n]*)?\]\]")


def evaluate(case: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    """Run one unscored input against actual isolated business services."""
    if any(key in case for key in ("expected", "rubric", "gold", "review")):
        raise ValueError("evaluation_answers_not_allowed_in_target_input")
    started = time.monotonic()
    if case["family"] == "memory":
        result = _evaluate_memory(case, provider)
    elif case["family"] == "retrieval":
        result = _evaluate_retrieval(case, provider)
    else:
        raise ValueError("unsupported_memory_evaluation_family")
    result["metrics"]["latency_seconds"] = round(time.monotonic() - started, 6)
    return result


def _neighbor(row: dict[str, Any]) -> LongTermMemory:
    return LongTermMemory(
        id=row["id"], content=row["content"], user_id="synthetic-eval-user",
        scope=MemoryScope.SHARED, persona_id=None, status=MemoryStatus.ACTIVE,
        created_at=None, updated_at=None,
        metadata={"journal_kind": row["kind"], "valid_from": row.get("valid_from"),
                  "source_created_at": row.get("observed_at"),
                  "reviewed_at": row.get("reviewed_at")},
    )


def _evaluate_memory(case: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    charged: list[int] = []
    evidence = JournalEvidence(
        id=fingerprint(case.get("case_id", case["text"])),
        source_id="riji/daily/synthetic", path="daily/synthetic.md", version="fixture-v1",
        kind=case.get("source_kind", "daily"), section="Notes", line=1,
        observed_at=case.get("observed_at"), text=case["text"],
        content_type=case.get("content_type", "personal_journal"),
    )
    neighbors = tuple(_neighbor(row) for row in case.get("supplied_neighbors", []))
    extractor = JournalMemoryExtractor(provider, charge=charged.append)
    scope = getattr(provider, "request_scope", nullcontext)
    with scope():
        candidates = extractor.extract(evidence)
        decisions = extractor.relate(candidates, neighbors) if candidates and neighbors else []
    known_ids = {item.id for item in neighbors}
    observed = {
        "status": "completed",
        "evidence_quotes_valid": all(q in evidence.text for c in candidates for q in c.quotes),
        "inferred_only_observation": all(c.kind == "observation" for c in candidates
                                         if c.certainty == "inferred"),
        "relation_targets_valid": all(d["target_id"] in known_ids if d["action"] != "new"
                                      else d["target_id"] is None for d in decisions),
    }
    return {
        "output": {
            "observed": observed, "family": "memory", "semantic_review": "unreviewed",
            "scope": "extraction_and_supplied_neighbor_relation_component_only",
            "real_mem0_retrieval": "not_run", "writes": "not_run",
            "candidates": [item.to_dict() for item in candidates], "decisions": decisions,
            "source_id": evidence.source_id, "source_kind": evidence.kind,
            "supplied_neighbor_ids": sorted(known_ids),
        },
        "metrics": {"memory_guarded_send_attempts": len(charged),
                    "memory_request_chars": sum(charged), "candidate_count": len(candidates),
                    "relation_decision_count": len(decisions)},
    }


class _RecordingRegistry(ToolRegistry):
    """Observe the real registry boundary without substituting tool results."""

    def __init__(self, service: RetrievalService) -> None:
        super().__init__(service)
        self.observations: list[dict[str, Any]] = []

    def invoke(self, context: ToolContext, name: str, arguments_json: str) -> ToolInvocation:
        result = super().invoke(context, name, arguments_json)
        try:
            arguments = json.loads(arguments_json)
        except (ValueError, TypeError):
            arguments = {"invalid_json": True}
        self.observations.append({"tool": name, "arguments": arguments, "ok": result.ok,
                                  "error": result.error, "source_ids": list(result.source_ids),
                                  "payload": result.payload})
        return result


def _write_notes(root: Path, notes: list[dict[str, Any]]) -> dict[str, str]:
    if not 0 <= len(notes) <= 50:
        raise ValueError("synthetic_note_limit_exceeded")
    for note in notes:
        relative = PurePosixPath(note["path"])
        if (relative.is_absolute() or ".." in relative.parts or "\\" in str(relative)
                or relative.suffix != ".md" or len(relative.parts) != 2
                or relative.parts[0] not in {"daily", "weekly", "monthly"}):
            raise ValueError("invalid_synthetic_note_path")
        content = note["markdown"]
        if not isinstance(content, str) or len(content) > 30000:
            raise ValueError("synthetic_note_size_exceeded")
        path = root / relative
        if path.exists():
            raise ValueError("duplicate_synthetic_note_path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return _hash_notes(root)


def _hash_notes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*.md")}


def _content_sources(observations: list[dict[str, Any]]) -> set[str]:
    return {source for item in observations
            if item["ok"] and item["tool"] != "list_periods"
            for source in item["source_ids"]}


def _evaluate_retrieval(case: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="riji-eval-fts-") as directory:
        root = Path(directory) / "riji"
        root.mkdir()
        before = _write_notes(root, case["notes"])
        index = JournalIndex(database_path=Path(directory) / "index.sqlite3", journal_root=root)
        try:
            index.build_index()
            registry = _RecordingRegistry(RetrievalService(index))
            context = ToolContext("eval-request", "eval-session", "synthetic-eval-user", "gentle_reviewer")
            runner = AgentRunner(provider, registry, limits=AgentLimits(max_rounds=6, max_tool_calls=12))
            question = f"当前评测日期：{case.get('as_of', '2026-09-11')}。\n{case['question']}"
            result = runner.run(context, question)
            private_ids = {f"riji/{Path(note['path']).with_suffix('')}" for note in case["notes"]
                           if (parsed := index.get(f"riji/{Path(note['path']).with_suffix('')}"))
                           is not None and parsed.private}
            return _retrieval_result(result, registry, private_ids, before == _hash_notes(root))
        finally:
            index.close()


def _retrieval_result(result: Any, registry: _RecordingRegistry,
                      private_ids: set[str], unchanged: bool) -> dict[str, Any]:
    citations = set(_CITATION.findall(result.answer))
    retrieved = _content_sources(registry.observations)
    observed = {
        "status": "completed", "vault_unchanged": unchanged,
        "private_sources_retrieved": sorted(set(result.sources) & private_ids),
        "citations_without_retrieved_content": sorted(citations - retrieved),
        "answer_present": bool(result.answer.strip()), "exceeded_rounds": result.exceeded_rounds,
    }
    return {
        "output": {
            "observed": observed, "family": "retrieval", "semantic_review": "unreviewed",
            "scope": "agent_runner_local_fts_only", "real_mem0_retrieval": "not_run",
            "embedding_retrieval": "not_run", "answer": result.answer,
            "source_ids": list(result.sources), "content_source_ids": sorted(retrieved),
            "cited_source_ids": sorted(citations), "tool_observations": registry.observations,
            "audit": [asdict(item) for item in result.audit],
        },
        "metrics": {"tool_calls": result.tool_calls, "agent_rounds": result.rounds,
                    "retrieved_source_count": len(retrieved), "cited_source_count": len(citations),
                    "tool_result_chars": sum(len(json.dumps(item["payload"], ensure_ascii=False))
                                             for item in registry.observations)},
    }
