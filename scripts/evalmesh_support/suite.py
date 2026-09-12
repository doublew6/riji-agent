"""Freeze local source fixtures and drive the installed EvalMesh contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
from importlib import metadata
import json
import os
from pathlib import Path
import sys
from typing import Any

from evalmesh_support.environment import ExecutionEnvironment, environment_policy, timing_observation
from evalmesh_support.private_io import make_private_directory, write_private_json
from evalmesh_support.providers import provider_route_policy


@dataclass(frozen=True)
class SuiteOptions:
    output: Path
    subject: Path
    harness: Path
    selection: str
    provider: str
    model: str
    memory_model: str
    repetitions: int
    live: bool
    execute: bool
    case_ids: tuple[str, ...] = ()


def read_cases(options: SuiteOptions) -> list[dict[str, Any]]:
    cases = []
    for name in ("boundaries", "memory", "mentors"):
        path = options.subject / "evals/agent-v1" / (name + ".jsonl")
        cases.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    if len({row["id"] for row in cases}) != len(cases):
        raise ValueError("duplicate_case")
    selected = []
    for row in cases:
        boundary = row["input"]["family"] == "boundary"
        tags = row.get("tags", [])
        include = {
            "boundary": boundary, "smoke": "smoke" in tags,
            "quality-regression": not boundary and "regression" in tags,
            "candidate": "acceptance-candidate" in tags, "all": True,
        }[options.selection]
        if include and (not options.case_ids or row["id"] in options.case_ids):
            selected.append(row)
    if not selected or set(options.case_ids) - {row["id"] for row in selected}:
        raise ValueError("empty_or_unknown_case_selection")
    if any(row["input"]["family"] != "boundary" for row in selected) and not options.live:
        if options.execute:
            raise ValueError("live_execution_requires_explicit_flag")
    return selected


def read_source(path: Path) -> bytes:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("snapshot_symlink_rejected")
    data = path.read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("snapshot_file_too_large")
    return data


def copy_tree(source: Path, destination: Path) -> dict[str, str]:
    if any(path.is_symlink() for path in (source, *source.parents)):
        raise ValueError("snapshot_symlink_rejected")
    hashes = {}
    destination.mkdir(mode=0o700, parents=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part.startswith(".") or part in {"__pycache__", "output"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("snapshot_symlink_rejected")
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".sqlite", ".sqlite3", ".db", ".log"}:
            continue
        data = read_source(path)
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    return hashes


def freeze_fixture(options: SuiteOptions) -> dict[str, Any]:
    fixture = options.output / "fixture"
    fixture.mkdir(mode=0o700)
    files = {
        "subject": copy_tree(options.subject / "src", fixture / "src"),
        "adapter": copy_tree(options.subject / "scripts/evalmesh_support", fixture / "evalmesh_support"),
        "harness": copy_tree(options.harness / "src", options.output / "harness/src"),
    }
    for name in ("evalmesh_adapter.py", "evaluate_agent_suite.py"):
        source = options.subject / "scripts" / name
        if source.is_symlink():
            raise ValueError("snapshot_symlink_rejected")
        data = read_source(source)
        destination = fixture if name == "evalmesh_adapter.py" else options.output
        (destination / name).write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    for name in ("pyproject.toml", "uv.lock"):
        data = read_source(options.subject / name)
        (options.output / name).write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    harness_config = read_source(options.harness / "pyproject.toml")
    (options.output / "harness/pyproject.toml").write_bytes(harness_config)
    files["harness-pyproject.toml"] = hashlib.sha256(harness_config).hexdigest()
    reviews = options.output / "reviews"
    reviews.mkdir(mode=0o700)
    for path in sorted((options.subject / "evals/agent-v1").glob("*-review.json")):
        if path.is_symlink():
            raise ValueError("snapshot_symlink_rejected")
        data = read_source(path)
        (reviews / path.name).write_bytes(data)
        (reviews / path.name).chmod(0o600)
        files["reviews/" + path.name] = hashlib.sha256(data).hexdigest()
    return files


def manifest_text(options: SuiteOptions, application_id: str = "snapshot-v1") -> str:
    argv = [sys.executable, "evalmesh_adapter.py", "--provider", options.provider,
            "--model", options.model, "--memory-model", options.memory_model]
    if options.live:
        argv.append("--live")
    identity = "controlled" if options.selection == "boundary" else options.provider
    model = "controlled" if options.selection == "boundary" else options.model
    fields = [
        "schema_version = 1", 'subject_id = "riji-agent"',
        f'suite_id = "agent-v1-{options.selection}"', 'case_files = ["cases.jsonl"]',
        f"repetitions = {options.repetitions}", "pass_threshold = 1.0",
        "[variant]", f'id = "{identity}-configured-v1"',
        f'model_id = "{model}"', f'application_id = "{application_id}"',
        "[target]", 'kind = "command"',
        "argv = " + json.dumps(argv), 'workspace_mode = "copy"',
        'workspace_path = "fixture"', 'output_mode = "json"',
        "timeout_seconds = 600", "max_output_bytes = 2097152",
        'forward_env = ["RIJI_EVAL_OUTPUT_DIR", "DEEPSEEK_API_KEY", "RIJI_EVAL_CODEX_HOME", "RIJI_EVAL_CODEX_BIN"]',
        "[privacy]", 'capture = "digest"', "include_metrics = true",
        "[[graders]]", 'id = "observed"', 'kind = "json_equals"',
        'actual_path = "observed"', "required = true",
    ]
    for name in ("provider_attempts", "application_request_chars"):
        fields.extend(["[[graders]]", f'id = "metric-{name}"',
                       'kind = "metric_threshold"', f'metric = "{name}"',
                       "min = 0", "required = false"])
    return "\n".join(fields) + "\n"


def prepare(options: SuiteOptions) -> list[dict[str, Any]]:
    cases = read_cases(options)
    if not 1 <= options.repetitions <= 3:
        raise ValueError("evaluation_repetitions_out_of_range")
    if options.provider not in {"deepseek", "codex"}:
        raise ValueError("evaluation_provider_invalid")
    make_private_directory(options.output)
    make_private_directory(options.output / "private-attempts")
    snapshot = freeze_fixture(options)
    policy_source = read_source(Path(provider_route_policy.__code__.co_filename))
    if snapshot["adapter"].get("providers.py") != hashlib.sha256(policy_source).hexdigest():
        raise ValueError("snapshot_provider_route_contract_mismatch")
    verify_environment_contract(snapshot)
    case_data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases)
    (options.output / "cases.jsonl").write_text(case_data)
    (options.output / "cases.jsonl").chmod(0o600)
    application_id = hashlib.sha256(json.dumps(snapshot["subject"], sort_keys=True).encode()).hexdigest()
    (options.output / "evalmesh.toml").write_text(manifest_text(options, "app-" + application_id[:20]))
    (options.output / "evalmesh.toml").chmod(0o600)
    receipt = {
        "schema_version": 1, "files": snapshot,
        "cases_sha256": hashlib.sha256(case_data.encode()).hexdigest(),
        "manifest_sha256": hashlib.sha256((options.output / "evalmesh.toml").read_bytes()).hexdigest(),
        "python_version": sys.version, "python_executable": sys.executable,
        "dependencies": dict(sorted((dist.metadata["Name"], dist.version)
                                    for dist in metadata.distributions() if dist.metadata["Name"])),
        "annotation": "assistant_authored_not_independently_validated",
        "blind_holdout": False, "case_count": len(cases),
        "attempt_count": len(cases) * options.repetitions,
        "environment_policy": environment_policy(
            any(row["input"]["family"] != "boundary" for row in cases)),
        "provider_route_policy": provider_route_policy(
            options.provider if any(row["input"]["family"] != "boundary" for row in cases)
            else "controlled"),
    }
    key = os.environ.get("EVALMESH_HMAC_KEY")
    if key:
        receipt["mac"] = hmac.new(key.encode(), json.dumps(receipt, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    write_private_json(options.output / "snapshot.json", receipt)
    return cases


def verify_snapshot(root: Path) -> None:
    receipt = json.loads((root / "snapshot.json").read_text())
    expected_mac = receipt.pop("mac", "")
    key = os.environ.get("EVALMESH_HMAC_KEY", "").encode()
    actual_mac = hmac.new(key, json.dumps(receipt, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    if not expected_mac or not hmac.compare_digest(expected_mac, actual_mac):
        raise ValueError("snapshot_seal_invalid")
    fixture_entries = {path.name for path in (root / "fixture").iterdir()
                       if path.name != "__pycache__"}
    if fixture_entries != {"src", "evalmesh_support", "evalmesh_adapter.py"}:
        raise ValueError("snapshot_file_set_changed")
    locations = {"subject": root / "fixture/src", "adapter": root / "fixture/evalmesh_support",
                 "harness": root / "harness/src"}
    for label, hashes in receipt["files"].items():
        if label in locations:
            if any(path.is_symlink() for path in locations[label].rglob("*")):
                raise ValueError("snapshot_symlink_rejected")
            actual_files = {str(path.relative_to(locations[label])) for path in locations[label].rglob("*")
                            if path.is_file() and "__pycache__" not in path.parts}
            if actual_files != set(hashes):
                raise ValueError("snapshot_file_set_changed")
            files = [(locations[label] / name, digest) for name, digest in hashes.items()]
        else:
            name = {"evalmesh_adapter.py": "fixture/evalmesh_adapter.py",
                    "harness-pyproject.toml": "harness/pyproject.toml"}.get(label, label)
            files = [(root / name, hashes)]
        if any(hashlib.sha256(read_source(path)).hexdigest() != digest for path, digest in files):
            raise ValueError("snapshot_content_changed")
    if hashlib.sha256(read_source(root / "cases.jsonl")).hexdigest() != receipt["cases_sha256"]:
        raise ValueError("snapshot_cases_changed")
    if hashlib.sha256(read_source(root / "evalmesh.toml")).hexdigest() != receipt["manifest_sha256"]:
        raise ValueError("snapshot_manifest_changed")


def build_review_index(root: Path, runs: Any) -> dict[str, Any]:
    records = [json.loads(path.read_text()) for path in sorted((root / "private-attempts").glob("*.json"))]
    rows = []
    for run in runs:
        start, end = datetime.fromisoformat(run.started_at), datetime.fromisoformat(run.completed_at)
        matches = [record for record in records if record["case_id"] == run.case_id
                   and start <= datetime.fromisoformat(record["started_at"]) <= end
                   and start <= datetime.fromisoformat(record["finished_at"]) <= end]
        rows.append({
            "run_id": run.run_id, "case_id": run.case_id, "attempt": run.attempt,
            "machine_passed": run.passed,
            "private_execution_id": matches[0]["execution_id"] if len(matches) == 1 else None,
            "association": "unique_serial_time_window" if len(matches) == 1 else "unavailable",
            "semantic_review": "unreviewed", "human_verdict": None,
        })
    return {"schema_version": 1, "rows": rows, "independent_human_review": False}



def verify_environment_contract(files: dict[str, Any]) -> None:
    """Reject old subject or runner helpers without executing subject code."""
    trusted = {
        "environment.py": Path(environment_policy.__code__.co_filename),
        "providers.py": Path(provider_route_policy.__code__.co_filename),
        "suite.py": Path(__file__),
    }
    for name, path in trusted.items():
        digest = hashlib.sha256(read_source(path)).hexdigest()
        if files.get("adapter", {}).get(name) != digest:
            raise ValueError("snapshot_environment_contract_mismatch")
    cli = Path(__file__).resolve().parents[1] / "evaluate_agent_suite.py"
    if files.get("evaluate_agent_suite.py") != hashlib.sha256(read_source(cli)).hexdigest():
        raise ValueError("snapshot_environment_contract_mismatch")


def read_attempt_timings(root: Path) -> list[dict[str, Any]]:
    timings = []
    for path in sorted((root / "private-attempts").glob("*.json")):
        record = json.loads(read_source(path))
        trace = record.get("model_trace", [])
        count = record.get("result", {}).get("metrics", {}).get("provider_attempts", 0)
        if type(count) is not int or count < 0 or not isinstance(trace, list):
            raise ValueError("evaluation_provider_timing_invalid")
        if count != len(trace):
            raise ValueError("evaluation_provider_timing_incomplete")
        for index, item in enumerate(trace):
            timing = timing_observation(item.get("timing", {}))
            timings.append({
                "case_id": record["case_id"], "execution_id": record["execution_id"],
                "provider_attempt": index + 1,
                **timing,
            })
    return timings


def finish_environment(environment: ExecutionEnvironment, options: SuiteOptions,
                       planned_attempts: int) -> dict[str, Any]:
    attempts = []
    record_count = None
    try:
        if environment.live:
            record_count = len(list((options.output / "private-attempts").glob("*.json")))
            if record_count != planned_attempts:
                environment.errors.append("attempt_record_count_mismatch")
            attempts = read_attempt_timings(options.output)
    except Exception:
        environment.errors.append("provider_timing_observation_failed")
    assessment = environment.assessment(attempts)
    assessment["planned_attempt_count"] = planned_attempts
    assessment["private_attempt_count"] = record_count
    write_private_json(options.output / "environment.json", assessment)
    return assessment


def run_suite(options: SuiteOptions) -> dict[str, Any]:
    if len(os.environ.get("EVALMESH_HMAC_KEY", "").encode()) < 32:
        raise ValueError("persistent_hmac_key_required")
    verify_snapshot(options.output)
    receipt = json.loads((options.output / "snapshot.json").read_text())
    verify_environment_contract(receipt["files"])
    if any((options.output / name).exists() for name in (
            "runs.jsonl", "summary.json", "review-index.json", "environment.json")):
        raise ValueError("evaluation_batch_already_started")
    if any((options.output / "private-attempts").iterdir()):
        raise ValueError("evaluation_batch_already_started")
    cases = [json.loads(line) for line in (options.output / "cases.jsonl").read_text().splitlines()]
    live = any(row["input"]["family"] != "boundary" for row in cases)
    if live and not options.live:
        raise ValueError("live_execution_requires_explicit_flag")
    if receipt.get("environment_policy") != environment_policy(live):
        raise ValueError("snapshot_environment_policy_mismatch")
    environment = ExecutionEnvironment(live)
    try:
        with environment:
            result = _execute_frozen_suite(options)
    finally:
        assessment = finish_environment(environment, options, receipt["attempt_count"])
    result["environment_ok"] = assessment["environment_ok"]
    result["environment_status"] = assessment["status"]
    return result


def _execute_frozen_suite(options: SuiteOptions) -> dict[str, Any]:
    sys.path.insert(0, str(options.output / "harness/src"))
    from evalmesh.analytics import summarize_runs
    from evalmesh.manifest import load_suite
    from evalmesh.reporters import JsonlReporter
    from evalmesh.runner import Runner
    from evalmesh.scorecard import build_scorecard, resource_assessment

    os.environ["RIJI_EVAL_OUTPUT_DIR"] = str(options.output / "private-attempts")
    manifest, cases = load_suite(options.output / "evalmesh.toml")
    batch = Runner(manifest, cases, (JsonlReporter(options.output / "runs.jsonl"),)).run()
    summary = summarize_runs(batch.runs)
    card = build_scorecard(summary, resources=resource_assessment(batch.runs))
    write_private_json(options.output / "summary.json", summary.to_dict())
    write_private_json(options.output / "scorecard.json", card)
    write_private_json(options.output / "review-index.json", build_review_index(options.output, batch.runs))
    verify_snapshot(options.output)
    return {"cases": summary.case_count, "attempts": summary.attempt_count,
            "machine_passed": summary.passed, "pass_at_1": summary.pass_at_1,
            "stable_pass_at_k": summary.stable_pass_at_k,
            "reporting_ok": batch.reporting_ok, "semantic_review": "unreviewed"}
