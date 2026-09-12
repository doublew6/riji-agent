"""Offline live-evaluation power ownership, timing and sealed-contract tests."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

from riji_agent.models.types import AssistantTurn, LLMError
from test_evalmesh_adapter import _options, modules  # noqa: F401


@pytest.fixture
def environment(modules: dict[str, Any]) -> Any:
    return importlib.import_module("evalmesh_support.environment")


class FakeClock:
    def __init__(self) -> None:
        self.wall = 1000.0
        self.active = 100.0

    def advance(self, wall: float, active: float) -> None:
        self.wall += wall
        self.active += active

    def install(self, module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(module.time, "time", lambda: self.wall)
        monkeypatch.setattr(module.time, "monotonic", lambda: self.active)


class FakeProcess:
    def __init__(self, *, alive: bool = True, ignore_terminate: bool = False) -> None:
        self.alive = alive
        self.ignore_terminate = ignore_terminate
        self.events: list[tuple[str, Any]] = []

    def poll(self) -> int | None:
        self.events.append(("poll", None))
        return None if self.alive else 0

    def wait(self, timeout: float) -> int:
        self.events.append(("wait", timeout))
        if self.alive:
            raise subprocess.TimeoutExpired("synthetic-caffeinate", timeout)
        return 0

    def terminate(self) -> None:
        self.events.append(("terminate", None))
        if not self.ignore_terminate:
            self.alive = False

    def kill(self) -> None:
        self.events.append(("kill", None))
        self.alive = False


@pytest.fixture
def assertion(environment: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    process = FakeProcess()
    calls = []
    monkeypatch.setattr(environment.sys, "platform", "darwin")

    def popen(*args: Any, **kwargs: Any) -> FakeProcess:
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(environment.subprocess, "Popen", popen)
    return process, calls


@pytest.mark.parametrize("exception", [None, ValueError, KeyboardInterrupt])
def test_assertion_is_owned_idle_only_and_released_for_all_exits(
    environment: Any,
    assertion: Any,
    exception: Any,
) -> None:
    process, calls = assertion
    guard = environment.ExecutionEnvironment(True)
    try:
        with guard:
            if exception:
                raise exception("synthetic-private-error")
    except (ValueError, KeyboardInterrupt):
        pass
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]
    assert kwargs["env"] == {} and kwargs["close_fds"] is True
    assert kwargs["stderr"] == kwargs["stdout"] == kwargs["stdin"] == subprocess.DEVNULL
    assert not process.alive
    assert [name for name, _ in process.events].count("terminate") == 1
    assert guard.assessment([])["environment_ok"] is True
    assert guard.assertion == "released"


def test_only_owned_child_is_killed_if_termination_does_not_finish(
    environment: Any,
    assertion: Any,
) -> None:
    process, calls = assertion
    process.ignore_terminate = True
    with environment.ExecutionEnvironment(True) as guard:
        pass
    assert len(calls) == 1
    assert [name for name, _ in process.events][-4:] == [
        "terminate",
        "wait",
        "kill",
        "wait",
    ]
    assert not process.alive and guard.assessment([])["environment_ok"]


@pytest.mark.parametrize("failure", ["spawn", "immediate_exit"])
def test_assertion_setup_fails_closed_and_omits_exception_text(
    failure: str,
    environment: Any,
    assertion: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _ = assertion
    if failure == "spawn":

        def failed(*args: Any, **kwargs: Any) -> None:
            raise OSError("synthetic-private-launch-details")

        monkeypatch.setattr(environment.subprocess, "Popen", failed)
    else:
        process.alive = False
    guard = environment.ExecutionEnvironment(True)
    with pytest.raises(
        RuntimeError, match="evaluation_idle_assertion_setup_failed"
    ) as caught:
        with guard:
            pytest.fail("setup failure must prevent execution")
    report = guard.assessment([])
    assert report["environment_ok"] is False
    assert "idle_assertion_setup_failed" in report["error_codes"]
    assert "synthetic-private" not in str(caught.value) + json.dumps(report)


def test_unexpected_assertion_exit_invalidates_environment(
    environment: Any,
    assertion: Any,
) -> None:
    process, _ = assertion
    with environment.ExecutionEnvironment(True) as guard:
        process.alive = False
    assert guard.assessment([])["error_codes"] == ["idle_assertion_exited_early"]
    assert not any(name in {"terminate", "kill"} for name, _ in process.events)


def test_assertion_cleanup_failure_is_not_a_clean_environment(
    environment: Any,
    assertion: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _ = assertion

    def failed() -> None:
        raise OSError("synthetic-private-cleanup-details")

    monkeypatch.setattr(process, "terminate", failed)
    with environment.ExecutionEnvironment(True) as guard:
        pass
    result = guard.assessment([])
    assert not result["environment_ok"]
    assert result["error_codes"] == ["idle_assertion_cleanup_failed"]
    assert "synthetic-private" not in json.dumps(result)


@pytest.mark.parametrize(
    "wall,active,valid",
    [
        (411, 411, True),
        (6000, 6000, True),
        (411, 1700, False),
        (1700, 411, False),
        (15, 10, True),
        (15.001, 10, False),
        (10, 15.001, False),
        (-1, 1, False),
        (1, -1, False),
        (float("nan"), 1, False),
        (1, float("inf"), False),
    ],
)
def test_clock_discontinuities_are_symmetric_and_not_a_duration_timeout(
    wall: float,
    active: float,
    valid: bool,
    environment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    clock.install(environment, monkeypatch)
    sample = environment.ClockSample.start()
    clock.advance(wall, active)
    timing = sample.finish()
    assert timing["valid"] is valid
    json.dumps(timing, allow_nan=False)


@pytest.mark.parametrize("failed", [False, True])
def test_provider_attempt_timing_retained_without_retry_or_output_changes(
    failed: bool,
    modules: dict[str, Any],
    environment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    clock.install(environment, monkeypatch)

    class Provider:
        calls = 0

        def complete(self, messages: Any, tools: Any) -> AssistantTurn:
            self.calls += 1
            clock.advance(1200, 30)
            if failed:
                raise LLMError("model_timeout")
            return AssistantTurn(content="synthetic answer", tool_calls=[])

    source = Provider()
    counted = modules["providers"].CountedProvider(source)
    if failed:
        with pytest.raises(LLMError, match="model_timeout"):
            counted.complete([], [])
        assert counted.failures[0]["error_code"] == "model_timeout"
    else:
        assert counted.complete([], []).content == "synthetic answer"
        assert not counted.failures
    assert counted.calls == source.calls == len(counted.trace) == 1
    assert counted.trace[0]["duration_ms"] == 30000
    assert counted.trace[0]["timing"]["wall_elapsed_seconds"] == 1200
    assert counted.trace[0]["timing"]["valid"] is False


def test_timing_survives_content_trace_budget(
    modules: dict[str, Any],
    environment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    clock.install(environment, monkeypatch)

    class Provider:
        def complete(self, *_: Any) -> AssistantTurn:
            clock.advance(99, 1)
            return AssistantTurn(content="x" * (4 * 1024 * 1024), tool_calls=[])

    counted = modules["providers"].CountedProvider(Provider())
    counted.complete([], [])
    assert counted.trace[0]["content_omitted"] is True
    assert counted.trace[0]["timing"]["clock_skew_seconds"] == 98
    assert counted.trace[0]["timing"]["valid"] is False


def prepared(
    modules: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    boundary: bool = False,
) -> Any:
    monkeypatch.setenv(
        "EVALMESH_HMAC_KEY", "synthetic-environment-seal-not-a-real-secret"
    )
    options = _options(
        modules,
        tmp_path,
        selection="boundary" if boundary else "smoke",
        execute=True,
        live=True,
    )
    modules["suite"].prepare(options)
    return options


def fake_machine_run(options: Any) -> dict[str, Any]:
    cases = [
        json.loads(line)
        for line in (options.output / "cases.jsonl").read_text().splitlines()
    ]
    for case in cases:
        path = options.output / "private-attempts" / (case["id"] + ".json")
        if not path.exists():
            path.write_text(
                json.dumps(
                    {
                        "case_id": case["id"],
                        "execution_id": case["id"],
                        "result": {"metrics": {"provider_attempts": 0}},
                        "model_trace": [],
                    }
                )
            )
    count = len(cases)
    summary = {"case_count": count, "attempt_count": count, "passed": True}
    (options.output / "summary.json").write_text(json.dumps(summary))
    return {
        "cases": count,
        "attempts": count,
        "machine_passed": True,
        "reporting_ok": True,
        "semantic_review": "unreviewed",
    }


def test_machine_result_and_denominator_survive_environment_failure(
    modules: dict[str, Any],
    environment: Any,
    assertion: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)
    clock = FakeClock()
    clock.install(environment, monkeypatch)

    def run(options: Any) -> dict[str, Any]:
        clock.advance(1700, 400)
        return fake_machine_run(options)

    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", run)
    result = modules["suite"].run_suite(options)
    assert result["machine_passed"] and result["attempts"] == 3
    assert result["environment_ok"] is False
    summary_bytes = (options.output / "summary.json").read_bytes()
    report = json.loads((options.output / "environment.json").read_text())
    assert report["planned_attempt_count"] == 3 and report["environment_ok"] is False
    assert report["error_codes"] == ["batch_clock_discontinuity"]
    assert (options.output / "environment.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="evaluation_batch_already_started"):
        modules["suite"].run_suite(options)
    assert (options.output / "summary.json").read_bytes() == summary_bytes


def test_attempt_jump_cannot_be_cancelled_by_opposite_batch_clock_jump(
    modules: dict[str, Any],
    environment: Any,
    assertion: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)
    clock = FakeClock()
    clock.install(environment, monkeypatch)

    def run(options: Any) -> dict[str, Any]:
        path = options.output / "private-attempts/memory-01.json"
        path.write_text(
            json.dumps(
                {
                    "case_id": "memory-01",
                    "execution_id": "synthetic-01",
                    "result": {"metrics": {"provider_attempts": 1}},
                    "model_trace": [
                        {
                            "timing": {
                                "wall_elapsed_seconds": 100,
                                "monotonic_elapsed_seconds": 1,
                                "clock_skew_seconds": 99,
                                "valid": False,
                            }
                        }
                    ],
                }
            )
        )
        clock.advance(100, 100)
        return fake_machine_run(options)

    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", run)
    result = modules["suite"].run_suite(options)
    assert result["environment_ok"] is False and result["machine_passed"]
    report = json.loads((options.output / "environment.json").read_text())
    assert report["error_codes"] == ["provider_clock_discontinuity"]
    assert report["provider_timings"][0]["provider_attempt"] == 1


@pytest.mark.parametrize("failure", ["setup", "execution", "early_exit"])
def test_suite_failure_paths_release_assertion_and_save_safe_environment_report(
    failure: str,
    modules: dict[str, Any],
    environment: Any,
    assertion: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)
    process, _ = assertion
    entered = []

    def run(options: Any) -> dict[str, Any]:
        entered.append(True)
        if failure == "execution":
            raise ValueError("synthetic-private-run-error")
        process.alive = False
        return fake_machine_run(options)

    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", run)
    if failure == "setup":
        process.alive = False
    if failure in {"setup", "execution"}:
        with pytest.raises((RuntimeError, ValueError)):
            modules["suite"].run_suite(options)
    else:
        assert not modules["suite"].run_suite(options)["environment_ok"]
    assert entered == ([] if failure == "setup" else [True])
    assert not process.alive
    text = (options.output / "environment.json").read_text()
    assert "synthetic-private" not in text
    assert json.loads(text)["idle_assertion"] != "active"


def test_boundary_has_no_assertion_no_clock_and_no_source_mutation(
    modules: dict[str, Any],
    environment: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_: Any, **__: Any) -> None:
        pytest.fail("boundary must not inspect live clock or launch a process")

    monkeypatch.setattr(environment.subprocess, "Popen", forbidden)
    monkeypatch.setattr(environment.ClockSample, "start", forbidden)
    options = prepared(modules, tmp_path, monkeypatch, boundary=True)
    before = {str(p): p.read_bytes() for p in options.subject.rglob("*") if p.is_file()}
    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", fake_machine_run)
    assert modules["suite"].run_suite(options)["environment_ok"]
    report = json.loads((options.output / "environment.json").read_text())
    assert report["status"] == report["idle_assertion"] == "not_required"
    assert report["batch_timing"] is None and report["provider_timings"] == []
    assert all(Path(p).read_bytes() == data for p, data in before.items())


def test_non_macos_live_still_checks_timing_without_assertion(
    environment: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment.sys, "platform", "linux")
    monkeypatch.setattr(
        environment.subprocess, "Popen", lambda *a, **k: pytest.fail("macOS only")
    )
    with environment.ExecutionEnvironment(True) as guard:
        pass
    assert guard.assessment([])["environment_ok"]
    assert guard.assertion == "not_applicable_non_macos"


def reseal(receipt: dict[str, Any], path: Path) -> None:
    receipt.pop("mac", None)
    key = os.environ["EVALMESH_HMAC_KEY"].encode()
    receipt["mac"] = hmac.new(
        key, json.dumps(receipt, sort_keys=True).encode(), hashlib.sha256
    ).hexdigest()
    path.write_text(json.dumps(receipt))


@pytest.mark.parametrize("name", ["environment.py", "suite.py"])
@pytest.mark.parametrize("mutation", ["missing", "different"])
def test_prepare_rejects_old_environment_subject_contract(
    name: str,
    mutation: str,
    modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    options = _options(modules, tmp_path)
    path = options.subject / "scripts/evalmesh_support" / name
    if mutation == "missing":
        path.unlink()
    else:
        path.write_text("raise RuntimeError('subject-code-must-not-execute')\n")
    with pytest.raises(ValueError, match="snapshot_environment_contract_mismatch"):
        modules["suite"].prepare(options)
    assert not (options.output / "snapshot.json").exists()


@pytest.mark.parametrize("name", ["environment.py", "suite.py", "providers.py"])
def test_run_prepared_rejects_validly_sealed_old_helper_without_running_it(
    name: str,
    modules: dict[str, Any],
    environment: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)
    path = options.output / "fixture/evalmesh_support" / name
    data = b"raise RuntimeError('frozen-code-must-not-execute')\n"
    path.write_bytes(data)
    receipt_path = options.output / "snapshot.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files"]["adapter"][name] = hashlib.sha256(data).hexdigest()
    reseal(receipt, receipt_path)
    monkeypatch.setattr(
        environment.subprocess, "Popen", lambda *a, **k: pytest.fail("before assertion")
    )
    with pytest.raises(ValueError, match="snapshot_environment_contract_mismatch"):
        modules["suite"].run_suite(options)
    assert not (options.output / "environment.json").exists()


def test_run_prepared_requires_sealed_policy_matching_actual_selected_cases(
    modules: dict[str, Any],
    environment: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)
    path = options.output / "snapshot.json"
    receipt = json.loads(path.read_text())
    assert receipt["environment_policy"] == environment.environment_policy(True)
    receipt["environment_policy"] = environment.environment_policy(False)
    reseal(receipt, path)
    with pytest.raises(ValueError, match="snapshot_environment_policy_mismatch"):
        modules["suite"].run_suite(options)


@pytest.mark.parametrize("environment_ok", [False, True])
def test_cli_requires_environment_success_even_when_machine_passes(
    environment_ok: bool,
    modules: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    result = {
        "machine_passed": True,
        "reporting_ok": True,
        "environment_ok": environment_ok,
        "attempts": 3,
    }
    monkeypatch.setattr(modules["cli"], "run_suite", lambda _: result)
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_agent_suite.py",
            "--output",
            str(tmp_path),
            "--execute",
            "--run-prepared",
            "--live",
        ],
    )
    assert modules["cli"].main() == int(not environment_ok)
    assert json.loads(capsys.readouterr().out)["attempts"] == 3


@pytest.mark.parametrize(
    "record",
    [
        "{",
        "[]",
        json.dumps({"model_trace": [None]}),
        json.dumps(
            {"model_trace": [], "result": {"metrics": {"provider_attempts": 1}}}
        ),
        json.dumps(
            {"model_trace": None, "result": {"metrics": {"provider_attempts": 1}}}
        ),
        json.dumps(
            {"model_trace": [], "result": {"metrics": {"provider_attempts": True}}}
        ),
    ],
)
def test_malformed_or_missing_attempt_timing_keeps_machine_result_and_safe_assessment(
    record: str,
    modules: dict[str, Any],
    assertion: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)

    def run(options: Any) -> dict[str, Any]:
        (options.output / "private-attempts/memory-01.json").write_text(record)
        return fake_machine_run(options)

    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", run)
    result = modules["suite"].run_suite(options)
    assert result["machine_passed"] and result["attempts"] == 3
    assert result["environment_ok"] is False
    report = json.loads((options.output / "environment.json").read_text())
    assert report["error_codes"] == ["provider_timing_observation_failed"]
    assert report["idle_assertion"] == "released"
    assert json.loads((options.output / "summary.json").read_text())["passed"] is True


def test_trace_without_timing_cannot_be_validated_as_clean(
    modules: dict[str, Any],
    assertion: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)

    def run(options: Any) -> dict[str, Any]:
        (options.output / "private-attempts/memory-01.json").write_text(
            json.dumps(
                {
                    "case_id": "memory-01",
                    "execution_id": "synthetic",
                    "result": {"metrics": {"provider_attempts": 1}},
                    "model_trace": [{}],
                }
            )
        )
        return fake_machine_run(options)

    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", run)
    result = modules["suite"].run_suite(options)
    assert result["environment_ok"] is False
    report = json.loads((options.output / "environment.json").read_text())
    assert report["provider_timings"][0]["valid"] is False
    assert report["provider_timings"][0]["wall_elapsed_seconds"] is None


@pytest.mark.parametrize(
    "timing",
    [
        {"valid": True},
        {"valid": True, "wall_elapsed_seconds": "synthetic-private-clock-text"},
        {
            "valid": True,
            "wall_elapsed_seconds": 10,
            "monotonic_elapsed_seconds": 1,
            "clock_skew_seconds": 0,
        },
        {
            "valid": True,
            "wall_elapsed_seconds": 100,
            "monotonic_elapsed_seconds": 1,
            "clock_skew_seconds": 99,
        },
        {
            "valid": True,
            "wall_elapsed_seconds": float("nan"),
            "monotonic_elapsed_seconds": 1,
            "clock_skew_seconds": 0,
        },
    ],
)
def test_timing_observation_revalidates_numeric_fields_and_omits_arbitrary_text(
    timing: dict[str, Any],
    environment: Any,
) -> None:
    safe = environment.timing_observation(timing)
    assert not safe["valid"]
    assert "synthetic-private" not in json.dumps(safe, allow_nan=False)


def test_run_prepared_rejects_different_frozen_cli_gate(
    modules: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)
    cli = options.output / "evaluate_agent_suite.py"
    cli.write_text('raise RuntimeError("do-not-run")\n')
    path = options.output / "snapshot.json"
    receipt = json.loads(path.read_text())
    receipt["files"]["evaluate_agent_suite.py"] = hashlib.sha256(
        cli.read_bytes()
    ).hexdigest()
    reseal(receipt, path)
    with pytest.raises(ValueError, match="snapshot_environment_contract_mismatch"):
        modules["suite"].run_suite(options)


def test_live_preparation_does_not_acquire_assertion_or_sample_clock(
    modules: dict[str, Any],
    environment: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("preparation must have no live environment side effects")

    monkeypatch.setattr(environment.subprocess, "Popen", forbidden)
    monkeypatch.setattr(environment.ClockSample, "start", forbidden)
    options = prepared(modules, tmp_path, monkeypatch)
    receipt = json.loads((options.output / "snapshot.json").read_text())
    assert receipt["environment_policy"] == environment.environment_policy(True)
    assert not (options.output / "environment.json").exists()


@pytest.mark.parametrize("missing", [1, 3])
def test_missing_entire_private_records_cannot_pass_environment_validation(
    missing: int,
    modules: dict[str, Any],
    assertion: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = prepared(modules, tmp_path, monkeypatch)

    def run(options: Any) -> dict[str, Any]:
        result = fake_machine_run(options)
        for path in sorted((options.output / "private-attempts").glob("*.json"))[
            :missing
        ]:
            path.unlink()
        return result

    monkeypatch.setattr(modules["suite"], "_execute_frozen_suite", run)
    result = modules["suite"].run_suite(options)
    assert result["machine_passed"] is True and result["attempts"] == 3
    assert result["environment_ok"] is False
    report = json.loads((options.output / "environment.json").read_text())
    assert report["error_codes"] == ["attempt_record_count_mismatch"]
    assert report["planned_attempt_count"] == 3
    assert report["private_attempt_count"] == 3 - missing
    assert json.loads((options.output / "summary.json").read_text())["passed"] is True
