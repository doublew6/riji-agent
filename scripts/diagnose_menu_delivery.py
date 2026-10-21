"""Temporary cloud-only reproduction; never a supported-platform acceptance run."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

import test_ios as runner


TARGET = "RijiUITests/DiaryFlowTests/testMobileRoundtableHostInvitationAndLocalSavePreview"
REPETITIONS = 10
BRANCH = "refs/heads/codex/ci-menu-delivery-diagnostics"
SUBSYSTEM = "com.riji.ci-probe"
CATEGORY = "host-menu-delivery"


def arguments(run: runner.Run, device: dict) -> list[str]:
    args = runner.xcode_arguments(run, "ui-smoke", device["device"]["udid"])
    args = [arg for arg in args if not arg.startswith("-only-testing:")]
    return args + ["-only-testing:" + TARGET]


def discover(run: runner.Run, device: dict) -> None:
    path = run.artifacts / "target-inventory.json"
    # Xcode lists filtered-out cases as disabled. Discover the unchanged complete
    # plan, validate it with the canonical runner, then select only this target.
    args = runner.xcode_arguments(run, "full", device["device"]["udid"])
    args[args.index("-resultBundlePath") + 1] = str(run.artifacts / "enumeration.xcresult")
    run.command("xcode-discovery", args + [
        "-enumerate-tests", "-test-enumeration-style", "flat",
        "-test-enumeration-format", "json", "-test-enumeration-output-path", str(path),
    ])
    document = json.loads(path.read_text(encoding="utf-8"))
    discovered = runner.xcode_inventory(document, "full")
    if TARGET not in discovered:
        raise runner.RunnerError("The original diagnostic target is missing from complete discovery.")
    runner.write_json(run.artifacts / "full-discovery.json", sorted(discovered))
    runner.write_json(run.artifacts / "requested.json", {
        "target": TARGET, "maximum_repetitions": REPETITIONS,
        "stop_on_first_failure": True, "retry_failed_tests": False,
        "acceptance_eligible": False,
    })


def audit_repetitions(output: str, returncode: int) -> dict:
    events = re.findall(r"^Test Case '-\[([^ ]+) ([^\]]+)\]' (started|passed|failed|skipped)", output, re.M)
    expected = ("RijiUITests.DiaryFlowTests", TARGET.rsplit("/", 1)[1])
    outcomes = []
    started = False
    for suite, method, status in events:
        if (suite, method) != expected or (outcomes and outcomes[-1] == "failed"):
            raise runner.RunnerError("Unexpected test or continuation after the first diagnostic failure.")
        if status == "started":
            if started:
                raise runner.RunnerError("Diagnostic repetition started before its predecessor finished.")
            started = True
        else:
            if not started or status == "skipped":
                raise runner.RunnerError("Diagnostic repetition is skipped or missing its start.")
            outcomes.append(status)
            started = False
    if started or not 1 <= len(outcomes) <= REPETITIONS:
        raise runner.RunnerError("Diagnostic repetitions are incomplete or absent.")
    if returncode == 0 and (len(outcomes) != REPETITIONS or set(outcomes) != {"passed"}):
        raise runner.RunnerError("A successful bounded run requires all original repetitions to pass.")
    return {"observed": len(outcomes), "outcomes": outcomes, "acceptance_eligible": False}


def execute_tests(run: runner.Run, report: dict) -> None:
    before = runner.refresh_ui_binaries(run, report["device"])
    output = run.command("xcode-tests", arguments(run, report["device"]) + [
        "-test-iterations", str(REPETITIONS), "-run-tests-until-failure",
        "-test-repetition-relaunch-enabled", "YES",
    ], check=False)
    report["test_exit_code"] = run.commands[-1]["returncode"]
    bundle = run.artifacts / "tests.xcresult"
    if not bundle.exists():
        raise runner.RunnerError("Diagnostic result bundle is missing.")
    results = {}
    for name in ("summary", "tests"):
        results[name] = run.json_command("diagnostic-" + name, [
            "xcrun", "xcresulttool", "get", "test-results", name,
            "--path", str(bundle), "--compact",
        ])
    # Preserve receipts even when the original assertion failed.
    after = runner.ui_binary_receipt(run, report["device"], "after")
    if before != after:
        raise runner.RunnerError("Diagnostic binaries changed during execution.")
    report["repetitions"] = audit_repetitions(output, report["test_exit_code"])
    devices = results["tests"].get("devices")
    if not devices or any(item.get("deviceId") != report["device"]["device"]["udid"] for item in devices):
        raise runner.RunnerError("Diagnostic results used an unexpected device or Simulator clone.")
    cases = runner.result_cases(results["tests"].get("testNodes", []))
    if not cases or any(identifier != TARGET for identifier, _ in cases):
        raise runner.RunnerError("Result bundle contains missing or unexpected diagnostic cases.")
    if results["summary"].get("skippedTests") != 0 or results["summary"].get("expectedFailures") != 0:
        raise runner.RunnerError("Diagnostic result contains skipped or expected-failure cases.")
    if report["test_exit_code"]:
        raise runner.RunnerError("Original UI assertion failed; retain this reproduction without retry.")
    if results["summary"].get("result") != "Passed" or any(status != "Passed" for _, status in cases):
        raise runner.RunnerError("Diagnostic log and result bundle disagree.")


def collect_trace(run: runner.Run, report: dict) -> None:
    execution = next((item for item in run.commands if item["name"] == "xcode-tests"), None)
    if execution is None:
        raise runner.RunnerError("No execution window exists for trace capture.")
    device = runner.owned_ui_device(run, report["device"], "trace")
    raw = run.command("touch-trace", [
        "xcrun", "simctl", "spawn", device["udid"], "log", "show",
        "--last", "60m", "--style", "json", "--info",
        "--predicate", 'subsystem == "' + SUBSYSTEM + '" AND category == "' + CATEGORY + '"',
    ])
    payload = re.sub(r"\Agetpwuid_r did not find a match for uid [0-9]+\r?\n", "", raw, count=1)
    entries = json.loads(payload)
    if not isinstance(entries, list) or any(
        not isinstance(item, dict) or any(not isinstance(item.get(key), str)
        for key in ("timestamp", "subsystem", "category", "eventMessage")) for item in entries
    ):
        raise runner.RunnerError("Trace capture returned malformed events.")
    started = datetime.fromisoformat(execution["started_at"])
    finished = datetime.fromisoformat(execution["finished_at"])
    current = [item for item in entries if item["subsystem"] == SUBSYSTEM and item["category"] == CATEGORY
               and started <= datetime.fromisoformat(item["timestamp"]) <= finished]
    runner.write_json(run.artifacts / "touch-trace-current.json", current)
    if not current:
        raise runner.RunnerError("No trace events were recorded during the original test window.")
    report["trace"] = {"status": "captured", "events": len(current), "sequence_audit_required": True}


def execute(run: runner.Run, report: dict) -> None:
    report["environment"] = runner.check_tools(run)
    report["device"] = runner.prepare_device(run)
    discover(run, report["device"])
    try:
        execute_tests(run, report)
    finally:
        original_error = sys.exc_info()[0] is not None
        try:
            collect_trace(run, report)
        except (Exception, KeyboardInterrupt) as error:
            report["trace"] = {"status": "failed", "error": str(error) or "Interrupted"}
            if not original_error:
                raise runner.RunnerError("Diagnostic capture failed; inspect retained evidence.") from error


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != BRANCH:
        raise runner.RunnerError("This diagnostic is limited to its isolated cloud branch.")
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts = root / "output/menu-delivery-diagnostic" / (stamp + "-" + uuid4().hex[:8])
    artifacts.mkdir(parents=True)
    report = {
        "status": "running", "acceptance_eligible": False,
        "head": os.environ["GITHUB_SHA"], "base": "cb05e4329c077c8636c978aa60a6a9e40ca15ff9",
        "run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "target": TARGET, "maximum_repetitions": REPETITIONS, "stop_on_first_failure": True,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    runner.write_json(artifacts / "diagnostic.json", report)
    cache = Path.home() / "Library/Caches/riji-ios-automated-tests"
    control = runner.ExecutionControl()
    with runner.controlled_signals(control):
        try:
            with runner.runner_lock(cache / "runner.lock") as lock_fd:
                run = runner.Run(root, artifacts, cache, lock_fd, control=control, smoke_tests=(TARGET,))
                with runner.verified_sources(run, report):
                    execute(run, report)
            report["status"] = "bounded_reproduction_finished_requires_sequence_audit"
        except (Exception, KeyboardInterrupt) as error:
            report.update(status="failed", error=str(error) or "Interrupted")
            print(report["error"], file=sys.stderr)
        finally:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            runner.write_json(artifacts / "diagnostic.json", report)
    return 0 if report["status"] == "bounded_reproduction_finished_requires_sequence_audit" else 1


if __name__ == "__main__":
    raise SystemExit(main())
