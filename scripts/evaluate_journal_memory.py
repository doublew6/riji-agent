"""Optional real-model contract probe over a fixed synthetic corpus only.

No vault scanning, Mem0 writes, service installation or production migration.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
import time
from pathlib import Path
import tempfile
from typing import Any, Callable

from riji_agent.config import load_settings
from riji_agent.memory.journal_extract import JournalMemoryExtractor
from riji_agent.memory.journal_types import JournalEvidence, fingerprint, utc_now
from riji_agent.memory.models import LongTermMemory, MemoryScope, MemoryStatus
from riji_agent.models.registry import build_memory_model_provider
from riji_agent.models.types import LLMError, LLMProvider

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/journal-memory/cases.json"
ACCEPTANCE = ROOT / "evals/journal-memory/acceptance/cases.json"
SCOPE = "extraction and supplied-neighbor relation judgement; not semantic retrieval or production acceptance"
SAFE_ERRORS = frozenset({
    "journal_invalid_model_output", "journal_invalid_model_json", "journal_incomplete_extraction",
    "journal_invalid_candidates", "journal_invalid_candidate", "journal_inference_must_be_observation",
    "journal_evidence_required", "journal_invalid_evidence_quote", "journal_invalid_fact_date",
    "journal_unsupported_fact_date", "journal_invalid_decisions", "journal_incomplete_decisions",
    "journal_invalid_decision", "journal_invalid_relation_target",
    "codex_quota_exhausted", "codex_login_required", "codex_timeout", "codex_unavailable",
    "codex_home_not_isolated", "codex_invalid_response", "codex_request_failed",
})


def _existing(row: dict[str, Any]) -> LongTermMemory:
    return LongTermMemory(row["id"], row["content"], "synthetic-evaluation", MemoryScope.SHARED, None,
                          MemoryStatus.ACTIVE, None, None,
                          {"journal_kind": row["kind"], "valid_from": row["valid_from"]})


def _safe_error(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    if isinstance(exc, LLMError):
        code = str(exc)
    if isinstance(code, str) and code in SAFE_ERRORS:
        return code
    return "model_probe_failed"


def evaluate_case(case: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    charged = []
    extractor = JournalMemoryExtractor(provider, charge=charged.append)
    started = time.monotonic()
    evidence = JournalEvidence(fingerprint(case["id"]), "synthetic", "daily/synthetic.md", "fixture-v1",
                               case.get("source_kind", "daily"),
                               "Notes", 1, case["observed_at"], case["text"])
    stages: dict[str, Any] = {}
    result: dict[str, Any] = {"id": case["id"], "review_required": case["review"],
                              "semantic_review": {"status": "unreviewed", "verdict": None},
                              "requested_provider": provider.provider_name, "requested_model": provider.model_name}
    try:
        scope = getattr(provider, "request_scope", nullcontext)
        with scope():
            candidates = _stage("extract", lambda: extractor.extract(evidence), charged, stages)
            existing = tuple(_existing(row) for row in case["existing"])
            decisions = _stage("relate", lambda: extractor.relate(candidates, existing), charged, stages) \
                if candidates and existing else []
        result.update(status="valid_contract", candidates=[item.to_dict() for item in candidates], decisions=decisions)
    except Exception as exc:
        result.update(status="failed", error=_safe_error(exc))
    result.update(latency_seconds=round(time.monotonic() - started, 3), request_chars=sum(charged),
                  guarded_send_attempts=len(charged), stages=stages)
    return result


def _stage(name: str, operation: Callable[[], Any], charged: list[int], stages: dict[str, Any]) -> Any:
    start, offset = time.monotonic(), len(charged)
    try:
        return operation()
    finally:
        stages[name] = {"latency_seconds": round(time.monotonic() - start, 3),
                        "request_chars": sum(charged[offset:]), "guarded_send_attempts": len(charged) - offset}


def _load_cases(suite: str, selected: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = ACCEPTANCE if suite == "acceptance" else CASES
    raw = path.read_bytes()
    payload = json.loads(raw)
    cases = payload["cases"] if suite == "acceptance" else payload
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or set(selected) - set(ids):
        raise ValueError("unknown_or_duplicate_case")
    chosen = [case for case in cases if not selected or case["id"] in selected]
    metadata = {"corpus": payload["id"] if suite == "acceptance" else "synthetic-v1",
                "suite": suite, "corpus_sha256": hashlib.sha256(raw).hexdigest(),
                "extractor_sha256": hashlib.sha256(
                    (ROOT / "src/riji_agent/memory/journal_extract.py").read_bytes()).hexdigest(),
                "annotation": payload["annotation"] if suite == "acceptance" else {
                    "origin": "development_prompt_tuning", "review_status": "not_blind_acceptance"},
                "selected_cases": [case["id"] for case in chosen]}
    return chosen, metadata


def _build_provider(name: str) -> tuple[LLMProvider, Path]:
    # Configuration validation only: no wiring, directory creation or store/service construction.
    settings = load_settings()
    selected = settings.model_copy(update={"memory_model_provider": name})
    return build_memory_model_provider(selected), settings.journal_root


def _validate_output(path: Path | None, journal_root: Path) -> Path:
    if path is None or path.is_symlink() or path.suffix.lower() != ".json":
        raise ValueError("choose_regular_json_output")
    resolved, root = path.expanduser().resolve(), journal_root.resolve()
    if resolved == root or root in resolved.parents or resolved in {CASES.resolve(), ACCEPTANCE.resolve()}:
        raise ValueError("output_must_not_overwrite_sources")
    return resolved


def _run(cases: list[dict[str, Any]], metadata: dict[str, Any], provider: LLMProvider, output: Path) -> int:
    results: list[dict[str, Any]] = []
    identity = {"requested_provider": provider.provider_name, "requested_model": provider.model_name,
                "model_identity_basis": "configured provider request; server model identity not exposed"}
    _save(output, results, {**metadata, **identity})
    for case in cases:
        result = evaluate_case(case, provider)
        results.append(result)
        print(f'{case["id"]}: {result["status"]}', flush=True)
        _save(output, results, {**metadata, **identity})
    return int(any(result["status"] == "failed" for result in results))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true", help="Explicitly send bundled synthetic cases to the selected provider.")
    parser.add_argument("--provider", choices=("deepseek", "codex"), default="deepseek")
    parser.add_argument("--suite", choices=("development", "acceptance"), default="development")
    parser.add_argument("--output", type=Path, help="Local JSON report; never placed in the diary root.")
    parser.add_argument("--case", action="append", default=[], help="Restrict to named synthetic cases.")
    args = parser.parse_args(argv)
    try:
        cases, metadata = _load_cases(args.suite, args.case)
    except (ValueError, KeyError, TypeError, OSError):
        parser.error("Invalid bundled suite or unknown case identifier.")
    if not args.run_model:
        print(json.dumps({"mode": "plan", "provider": args.provider, "cases": [item["id"] for item in cases],
                          "cloud_calls": 0, **metadata}, indent=2))
        return 0
    if args.output is None:
        parser.error("Choose --output outside the diary root.")
    try:
        provider, journal_root = _build_provider(args.provider)
        output = _validate_output(args.output, journal_root)
        return _run(cases, metadata, provider, output)
    except Exception:
        parser.error("Evaluation setup or report write failed; inspect local configuration and output permissions.")


def _save(path: Path, results: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = {"generated_at": utc_now(), **metadata, "scope": SCOPE, "results": results,
            "request_char_basis": "application messages plus transmitted business schema at send guard; excludes provider envelope and transport",
            "billing_tokens": None, "monetary_cost": None,
            "semantic_quality": {"status": "unreviewed", "score": None},
            "coverage": {"semantic_retrieval": "not_run", "four_pipeline_comparison": "not_run",
                         "end_to_end_answers": "not_run"}}
    handle, temporary = tempfile.mkstemp(prefix=".journal-eval-", dir=path.parent)
    try:
        with os.fdopen(handle, "w") as output:
            json.dump(body, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
