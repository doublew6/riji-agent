"""Execute one synthetic case through real Riji service boundaries."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str((_HERE / "src") if (_HERE / "src").is_dir() else _HERE.parent / "src"))

from evalmesh_support.private_io import check_private_path, write_private_json
from evalmesh_support.providers import build_provider, failure_observation, provider_route_policy


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("evaluation_duplicate_json_key")
        result[key] = value
    return result


def read_case() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("evaluation_case_too_large")
    value = json.loads(raw, object_pairs_hook=unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (not isinstance(value, dict) or set(value) != {"protocol", "case_id", "input"}
            or value["protocol"] != "evalmesh.case.v1"
            or not isinstance(value["case_id"], str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value["case_id"]) is None
            or not isinstance(value["input"], dict)
            or value["input"].get("data_class") != "synthetic"
            or {"expected", "rubric", "gold"} & value["input"].keys()):
        raise ValueError("evaluation_case_invalid")
    return value


def dispatch(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    family = case.get("family")
    if family == "boundary":
        from evalmesh_support.boundaries import evaluate
        provider = None
    elif family in {"memory", "retrieval", "mentor"} and args.live:
        if family == "mentor":
            from evalmesh_support.mentors import evaluate
        else:
            from evalmesh_support.memory import evaluate
        model = args.memory_model if family == "memory" else args.model
        provider = build_provider(args.provider, model, args.model_timeout)
    else:
        raise ValueError("evaluation_live_required_or_unknown_family")
    try:
        result = evaluate(case, provider)
    except Exception as error:
        result = failure_observation(error)
    if provider is not None:
        result.setdefault("metrics", {}).update(
            provider_attempts=provider.calls,
            application_request_chars=provider.request_chars,
        )
        result["output"]["requested_provider"] = provider.provider_name
        result["output"]["requested_model"] = provider.model_name
        result["output"]["provider_route_policy"] = provider_route_policy(args.provider)
        response_mode = getattr(provider, "response_mode", None)
        if response_mode in ("sse", "json"):
            result["output"]["provider_response_mode"] = response_mode
        result["output"]["provider_errors"] = provider.failures
        result["output"]["_private_model_trace"] = provider.trace
    return result


def validate_result(result: dict[str, Any]) -> None:
    if set(result) != {"output", "metrics"} or not isinstance(result["output"], dict):
        raise ValueError("evaluation_result_invalid")
    if not isinstance(result["metrics"], dict):
        raise ValueError("evaluation_result_invalid")
    for key, value in result["metrics"].items():
        if (not isinstance(key, str) or type(value) not in {int, float}
                or not math.isfinite(value)):
            raise ValueError("evaluation_metric_invalid")


def execute(args: argparse.Namespace) -> int:
    envelope = read_case()
    directory = check_private_path(Path(os.environ["RIJI_EVAL_OUTPUT_DIR"]))
    if not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError("evaluation_private_output_invalid")
    started = datetime.now(timezone.utc).isoformat()
    execution_id = uuid.uuid4().hex
    case = dict(envelope["input"], case_id=envelope["case_id"])
    try:
        result = dispatch(case, args)
        validate_result(result)
    except Exception as error:
        result = {"output": {"observed": {"status": "adapter_error"},
                             "error_category": type(error).__name__}, "metrics": {}}
    result["output"]["execution_id"] = execution_id
    model_trace = result["output"].pop("_private_model_trace", [])
    record = {
        "schema_version": 1, "case_id": envelope["case_id"],
        "execution_id": execution_id, "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": hashlib.sha256(json.dumps(envelope["input"], sort_keys=True).encode()).hexdigest(),
        "data_class": "synthetic", "semantic_review": "unreviewed", "result": result,
        "model_trace": model_trace,
    }
    write_private_json(directory / (execution_id + ".json"), record)
    print(json.dumps({"protocol": "evalmesh.result.v1", **result}, ensure_ascii=False, allow_nan=False))
    return int(result["output"].get("observed", {}).get("status") in {"adapter_error", "failed"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider", choices=("deepseek", "codex"), default="deepseek")
    parser.add_argument("--model", default="deepseek-reasoner")
    parser.add_argument("--memory-model", default="deepseek-chat")
    parser.add_argument("--model-timeout", type=float, default=90)
    args = parser.parse_args()
    try:
        return execute(args)
    except Exception:
        print("evaluation_setup_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
