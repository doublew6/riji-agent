"""Portable fake-tool checks; this module never invokes Xcode or Simulator."""

import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("riji_ios_test_runner", ROOT / "scripts/test_ios.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-26-2"
TYPE = "com.apple.CoreSimulator.SimDeviceType.iPhone-17"
UDID = "11111111-1111-1111-1111-111111111111"


def inventory(devices=(), available=True):
    return ({"devices": {RUNTIME: list(devices)}},
            {"runtimes": [{"identifier": RUNTIME, "version": "26.2", "isAvailable": available}]},
            {"devicetypes": [{"identifier": TYPE, "name": "iPhone 17",
                              "minRuntimeVersion": 26 << 16, "maxRuntimeVersion": (26 << 16) | 65535}]})


def device(**changes):
    return {"name": runner.DEVICE_NAME, "udid": UDID, "state": "Booted",
            "deviceTypeIdentifier": TYPE, "isAvailable": True, **changes}


def result_documents(suite="smoke"):
    ui = list(runner.SMOKE_TESTS) if suite in {"smoke", "ui-smoke"} else ["RijiUITests/DiaryFlowTests/testFull"]
    identifiers = ["RijiNativeTests/NativeTests/testNative"] + ([] if suite == "native" else ui)
    bundles = {}
    for identifier in identifiers:
        bundle, class_name, method = identifier.split("/")
        bundles.setdefault(bundle, []).append({"nodeType": "Test Case", "result": "Passed",
                                              "nodeIdentifier": class_name + "/" + method + "()"})
    nodes = [{"name": name, "nodeType": "UI test bundle" if name == "RijiUITests" else "Unit test bundle",
              "children": cases} for name, cases in bundles.items()]
    summary = {"result": "Passed", "passedTests": len(identifiers), "totalTestCount": len(identifiers),
               "failedTests": 0, "skippedTests": 0, "expectedFailures": 0}
    return summary, {"devices": [{"deviceId": UDID}], "testNodes": nodes}


def test_existing_dedicated_device_is_reused_without_manual_device():
    choice = runner.select_device(*inventory([device(), device(name="Riji Development iPhone", udid="manual")]))
    assert choice["created"] is False
    assert choice["device"]["udid"] == UDID


@pytest.mark.parametrize("devices,available,message", [
    ([device(), device(udid="duplicate")], True, "Multiple"),
    ([device(isAvailable=False)], True, "unavailable"),
    ([device()], False, "unavailable"),
    ([device(state="Booting")], True, "transitioning"),
    ([], False, "No available"),
    ([device(deviceTypeIdentifier="ipad")], True, "compatible iPhone"),
])
def test_invalid_inventory_fails_without_replacement(devices, available, message):
    with pytest.raises(runner.RunnerError, match=message):
        runner.select_device(*inventory(devices, available))


def test_creation_chooses_latest_compatible_runtime_not_future_or_legacy_type():
    devices, runtimes, types = inventory()
    runtimes["runtimes"].append({"identifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0",
                                  "version": "27.0", "isAvailable": True})
    types["devicetypes"].extend([
        {"identifier": "legacy", "name": "iPhone 6", "minRuntimeVersion": 9 << 16, "maxRuntimeVersion": 15 << 16},
        {"identifier": "future", "name": "iPhone future", "minRuntimeVersion": 28 << 16, "maxRuntimeVersion": 0xffffffff},
    ])
    choice = runner.select_device(devices, runtimes, types)
    assert choice["created"]
    assert choice["runtime"]["identifier"] == RUNTIME
    assert choice["type"]["identifier"] == TYPE
    types["devicetypes"] = []
    with pytest.raises(runner.RunnerError, match="No compatible"):
        runner.select_device(devices, runtimes, types)


@pytest.mark.parametrize("suite", ["native", "smoke", "ui-smoke", "full"])
def test_xcode_audit_accepts_only_complete_requested_suites(suite):
    summary, tests = result_documents(suite)
    result = runner.audit_xcode(summary, tests, suite, UDID, set(identifier for identifier, _ in runner.result_cases(tests["testNodes"])))
    assert result["passed"] == summary["passedTests"]
    assert result["skipped"] == 0


@pytest.mark.parametrize("mutation,message", [
    ("skip", "failed, skipped"), ("failure", "failed, skipped"),
    ("missing-smoke", "smoke tests are missing"), ("zero", "zero executed"),
    ("count", "counts disagree"), ("clone", "unexpected device"),
    ("no-native", "requested test bundles"), ("duplicate", "duplicate"),
    ("summary-skip", "skippedTests"), ("expected-failure", "expectedFailures"),
])
@pytest.mark.parametrize("suite", ["smoke", "ui-smoke"])
def test_xcode_audit_rejects_false_green_results(mutation, message, suite):
    summary, tests = result_documents(suite)
    expected = {identifier for identifier, _ in runner.result_cases(tests["testNodes"])}
    if mutation in {"skip", "failure"}:
        tests["testNodes"][1]["children"][0]["result"] = "Skipped" if mutation == "skip" else "Failed"
    elif mutation == "missing-smoke":
        tests["testNodes"][1]["children"].pop()
        summary["passedTests"] -= 1
        summary["totalTestCount"] -= 1
    elif mutation == "zero":
        tests["testNodes"] = []
    elif mutation == "count":
        summary["passedTests"] += 1
    elif mutation == "clone":
        tests["devices"][0]["deviceId"] = "clone"
    elif mutation == "no-native":
        tests["testNodes"].pop(0)
        summary["passedTests"] -= 1
        summary["totalTestCount"] -= 1
    elif mutation == "duplicate":
        tests["testNodes"][0]["children"] *= 2
        summary["passedTests"] += 1
        summary["totalTestCount"] += 1
    else:
        summary["skippedTests" if mutation == "summary-skip" else "expectedFailures"] = 1
    with pytest.raises(runner.RunnerError, match=message):
        runner.audit_xcode(summary, tests, suite, UDID, expected)


def package_example(style="objc"):
    expected = {"RijiCoreTests.CoreTests/testCore": "RijiCoreTests",
                "RijiStateTests.StateTests/testState": "RijiStateTests",
                "RijiICloudProbeSupportTests.ProbeTests/testProbe": "RijiICloudProbeSupportTests"}
    lines = []
    for key, target in expected.items():
        class_name, method = key.split(".", 1)[1].split("/")
        name = ("-[" + target + "." + class_name + " " + method + "]" if style == "objc"
                else target + "." + class_name + "." + method)
        lines.extend(["Test Case '" + name + "' started.", "Test Case '" + name + "' passed (0.1 seconds)."])
    return expected, "\n".join(lines) + "\nExecuted 0 tests, with 0 failures\n"


@pytest.mark.parametrize("style", ["objc", "dotted"])
def test_package_audit_uses_every_case_instead_of_last_zero_footer(style):
    expected, output = package_example(style)
    result = runner.audit_package(output, expected)
    assert result["passed"] == 3
    assert set(result["targets"]) == runner.PACKAGE_TARGETS
    discovery = "\n".join(expected)
    assert runner.package_inventory(discovery) == expected


@pytest.mark.parametrize("mutation", ["missing", "skipped", "failed", "duplicate", "zero", "wrong-target"])
def test_package_audit_fails_closed(mutation):
    expected, output = package_example()
    if mutation == "missing":
        output = "\n".join(output.splitlines()[1:])
    elif mutation == "duplicate":
        output += output
    elif mutation == "zero":
        output = "Executed 0 tests, with 0 failures"
    elif mutation == "wrong-target":
        output = output.replace("RijiCoreTests", "WrongTests")
    else:
        output = output.replace("passed", mutation, 1)
    with pytest.raises(runner.RunnerError):
        runner.audit_package(output, expected)


def test_discovery_cannot_silently_omit_a_package_target():
    with pytest.raises(runner.RunnerError, match="all Core, State and Probe"):
        runner.package_inventory("RijiCoreTests.CoreTests/testCore\n")


def make_run(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    return runner.Run(ROOT, artifacts, tmp_path / "cache")


def test_real_subprocess_failure_retains_log_and_exit_code_without_tee(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(runner.RunnerError, match="exit 9"):
        run.command("fake-failure", [sys.executable, "-c", "print('synthetic failure'); raise SystemExit(9)"])
    assert "synthetic failure" in (run.artifacts / "fake-failure.log").read_text(encoding="utf-8")
    assert json.loads((run.artifacts / "commands.json").read_text(encoding="utf-8"))[0]["returncode"] == 9


def test_production_and_manual_fixture_environment_is_not_forwarded(tmp_path, monkeypatch):
    monkeypatch.setenv("RIJI_SIMULATOR_VAULT_ROOT", "synthetic-private-path")
    monkeypatch.setenv("RIJI_MOBILE_INSTALLATION", "synthetic-private-config")
    monkeypatch.setenv("SIMCTL_CHILD_RIJI_SAMPLE_MODE", "1")
    monkeypatch.setenv("IOS_RUNNER_ALLOWED_VALUE", "synthetic-allowed")
    run = make_run(tmp_path)
    output = run.command("environment", [sys.executable, "-c",
        "import os,json; print(json.dumps({k:v for k,v in os.environ.items() "
        "if k.startswith(('RIJI_', 'SIMCTL_CHILD_')) or k == 'IOS_RUNNER_ALLOWED_VALUE'}))"])
    assert json.loads(output) == {"IOS_RUNNER_ALLOWED_VALUE": "synthetic-allowed"}


@pytest.mark.parametrize("suite", ["native", "smoke", "ui-smoke", "full"])
def test_test_commands_target_only_dedicated_device_disable_clones_and_execute_tests(tmp_path, suite):
    run = make_run(tmp_path)
    args = runner.xcode_arguments(run, suite, UDID)
    assert "test" in args and "build" not in args
    assert args[args.index("-destination") + 1] == "platform=iOS Simulator,id=" + UDID
    assert args[args.index("-parallel-testing-enabled") + 1] == "NO"
    assert args[args.index("-maximum-parallel-testing-workers") + 1] == "1"
    assert "-only-testing:RijiNativeTests" in args
    assert not any(word in " ".join(args) for word in ("Development iPhone", "erase", "uninstall", "kill"))
    if suite in {"smoke", "ui-smoke"}:
        assert {arg.removeprefix("-only-testing:") for arg in args if arg.startswith("-only-testing:RijiUITests/")} == set(runner.SMOKE_TESTS)


def test_smoke_methods_exist_in_current_synthetic_fixture_class():
    source = (ROOT / "ios/RijiUITests/DiaryFlowTests.swift").read_text(encoding="utf-8")
    for method in runner.SMOKE_METHODS:
        assert "func " + method + "(" in source
    assert 'container = UUID().uuidString' in source
    assert 'app.launchEnvironment["RIJI_TEST_CONTAINER"] = container' in source


def test_fake_device_preparation_reuses_device_and_never_mutates_manual_simulator(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    fixtures = dict(zip(("devices", "runtimes", "devicetypes"), inventory([device()])))
    calls = []

    def command(name, args, check=True):
        calls.append(args)
        return json.dumps(fixtures[name]) if name in fixtures else ""

    monkeypatch.setattr(run, "command", command)
    choice = runner.prepare_device(run)
    assert not choice["created"]
    assert calls[-1] == ["xcrun", "simctl", "bootstatus", UDID, "-b"]
    assert all(args[2] in {"list", "bootstatus"} for args in calls)


def test_missing_xcode_fails_before_simulator_access(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    run.environment["DEVELOPER_DIR"] = str(tmp_path / "CommandLineTools")
    monkeypatch.setattr(run, "command", lambda *args, **kwargs: pytest.fail("No tools should run"))
    with pytest.raises(runner.RunnerError, match="Full Xcode"):
        runner.check_tools(run)


@pytest.mark.parametrize("suite,stages", [("core", ["tools", "package"]),
                                          ("native", ["tools", "device", "xcode"]),
                                          ("smoke", ["tools", "device", "package", "xcode"]),
                                          ("ui-smoke", ["tools", "device", "xcode"]),
                                          ("full", ["tools", "device", "package", "xcode"])])
def test_suite_dispatch_runs_requested_stages_without_device_for_core(tmp_path, monkeypatch, suite, stages):
    seen = []
    for name, stage in (("check_tools", "tools"), ("prepare_device", "device"),
                        ("execute_package", "package"), ("execute_xcode", "xcode")):
        monkeypatch.setattr(runner, name, lambda *args, stage=stage: seen.append(stage) or {})
    report = {}
    runner.execute_suite(make_run(tmp_path), suite, report)
    assert seen == stages
    assert ("package" in report) == ("package" in stages)
    assert ("xcode" in report) == ("xcode" in stages)


def test_failed_xcode_command_cannot_be_overruled_by_passing_result_bundle(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    (run.artifacts / "tests.xcresult").mkdir()
    summary, cases = result_documents()
    monkeypatch.setattr(runner, "enumerate_xcode", lambda *args: set())

    def command(name, args, check=True):
        run.commands.append({"returncode": 65})
        return ""

    monkeypatch.setattr(run, "command", command)
    monkeypatch.setattr(run, "json_command", lambda name, args: summary if name == "xcode-summary" else cases)
    with pytest.raises(runner.RunnerError, match="exit 65"):
        runner.execute_xcode(run, "smoke", {"device": device()})


def test_second_lock_owner_is_rejected_without_truncating_or_removing_lock(tmp_path, monkeypatch):
    lock = tmp_path / "runner.lock"
    lock.write_text("first-owner\n")

    def occupied(fd, flags):
        raise BlockingIOError("synthetic contention")

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=occupied))
    with pytest.raises(runner.RunnerError, match="Another iOS runner"):
        with runner.runner_lock(lock):
            pytest.fail("Second owner must not proceed")
    assert lock.read_text(encoding="utf-8") == "first-owner\n"


@pytest.mark.parametrize("suite", ["native", "smoke", "ui-smoke", "full"])
def test_missing_native_case_fails_even_when_bundle_and_summary_look_successful(suite):
    summary, tests = result_documents(suite)
    expected = {identifier for identifier, _ in runner.result_cases(tests["testNodes"])}
    expected.add("RijiNativeTests/AnotherClass/testMissing")
    with pytest.raises(runner.RunnerError, match="complete requested inventory"):
        runner.audit_xcode(summary, tests, suite, UDID, expected)


def test_full_ui_missing_case_fails_inventory_comparison():
    summary, tests = result_documents("full")
    expected = {identifier for identifier, _ in runner.result_cases(tests["testNodes"])}
    expected.add("RijiUITests/DiaryFlowTests/testMissing")
    with pytest.raises(runner.RunnerError, match="complete requested inventory"):
        runner.audit_xcode(summary, tests, "full", UDID, expected)


def enumeration():
    identifiers = ["RijiNativeTests/NativeTests/testNative", *runner.SMOKE_TESTS,
                   "RijiUITests/DiaryFlowTests/testAdditionalFullFlow"]
    return {"errors": [], "values": [{"disabledTests": [], "enabledTests": [
        {"identifier": name + "()"} for name in identifiers]}]}


def test_xcode_enumeration_includes_all_native_and_selects_only_requested_ui():
    document = enumeration()
    native = {"errors": [], "values": [{"disabledTests": [], "enabledTests": [
        {"identifier": "RijiNativeTests/NativeTests/testNative()"}]}]}
    assert runner.xcode_inventory(native, "native") == {"RijiNativeTests/NativeTests/testNative"}
    assert len(runner.xcode_inventory(document, "smoke")) == len(runner.SMOKE_TESTS) + 1
    assert len(runner.xcode_inventory(document, "ui-smoke")) == len(runner.SMOKE_TESTS) + 1
    assert len(runner.xcode_inventory(document, "full")) == len(runner.SMOKE_TESTS) + 2


@pytest.mark.parametrize("count", [1, 3, 4])
def test_smoke_partitions_are_disjoint_and_cover_the_complete_suite(count):
    partitions = [set(runner.smoke_partition(index, count)) for index in range(1, count + 1)]
    assert set.union(*partitions) == set(runner.SMOKE_TESTS)
    assert sum(map(len, partitions)) == len(runner.SMOKE_TESTS)
    assert max(map(len, partitions)) - min(map(len, partitions)) <= 1
    assert runner.smoke_partition(1, 1) == runner.SMOKE_TESTS


def test_ci_shard_configuration_dispatches_every_smoke_test_exactly_once():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ios.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert set(jobs) == {"core", "ui-smoke", "full"}
    core, ui, full = (jobs[name] for name in ("core", "ui-smoke", "full"))
    assert core["if"] == ui["if"] == "${{ inputs.suite != 'full' }}"
    assert full["if"] == "${{ inputs.suite == 'full' }}"
    assert ui["strategy"]["fail-fast"] is False
    assert all("needs" not in job and not job.get("continue-on-error", False) for job in jobs.values())
    for name, job in jobs.items():
        runs = [step for step in job["steps"] if "run" in step]
        assert len(runs) == 1 and "if" not in runs[0]
        assert f"python3 scripts/test_ios.py --suite {name}" in runs[0]["run"]
        assert all(not step.get("continue-on-error", False) for step in job["steps"])
        assert runs[0]["timeout-minutes"] == 55 and job["timeout-minutes"] == 60
    assert runner.RUN_TIMEOUT_SECONDS == 50 * 60
    indices = ui["strategy"]["matrix"]["shard"]
    denominator = re.fullmatch(r"\$\{\{ matrix.shard \}\}/(\d+)", ui["env"]["IOS_SMOKE_SHARD"])
    assert denominator
    count = int(denominator[1])
    assert indices == list(range(1, count + 1)) and count == 4
    assert '--smoke-shard "$IOS_SMOKE_SHARD"' in next(step["run"] for step in ui["steps"] if "run" in step)
    requested = []
    for index in indices:
        args, shard, tests = runner.parse_arguments(["--suite", "ui-smoke", "--smoke-shard", f"{index}/{count}"])
        assert args.suite == "ui-smoke" and shard == (index, count)
        requested.append(tests)
    assert list(map(len, requested)) == [11, 10, 10, 10]
    assert set().union(*map(set, requested)) == set(runner.SMOKE_TESTS)
    assert sum(map(len, requested)) == len(runner.SMOKE_TESTS)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
@pytest.mark.parametrize("suite", ["smoke", "ui-smoke"])
def test_shard_arguments_inventory_and_result_audit_agree(tmp_path, index, suite):
    run = make_run(tmp_path)
    run.smoke_tests = runner.smoke_partition(index, 4)
    args = runner.xcode_arguments(run, suite, UDID)
    selected = {arg.removeprefix("-only-testing:") for arg in args
                if arg.startswith("-only-testing:RijiUITests/")}
    assert selected == set(run.smoke_tests)
    assert "-only-testing:RijiNativeTests" in args
    expected = runner.xcode_inventory(enumeration(), suite, run.smoke_tests)
    assert expected == selected | {"RijiNativeTests/NativeTests/testNative"}
    summary, results = result_documents()
    ui = results["testNodes"][1]["children"]
    results["testNodes"][1]["children"] = [case for case in ui
        if "RijiUITests/" + case["nodeIdentifier"].removesuffix("()") in selected]
    summary.update(passedTests=len(expected), totalTestCount=len(expected))
    assert runner.audit_xcode(summary, results, suite, UDID, expected)["passed"] == len(expected)
    results["testNodes"][1]["children"].pop()
    summary.update(passedTests=len(expected) - 1, totalTestCount=len(expected) - 1)
    with pytest.raises(runner.RunnerError, match="smoke tests are missing"):
        runner.audit_xcode(summary, results, suite, UDID, expected)


@pytest.mark.parametrize("suite", ["smoke", "ui-smoke"])
def test_shard_still_requires_discovery_of_the_entire_smoke_suite(suite):
    document = enumeration()
    selected = runner.smoke_partition(1, 4)
    missing = next(test for test in runner.SMOKE_TESTS if test not in selected)
    document["values"][0]["enabledTests"] = [test for test in document["values"][0]["enabledTests"]
        if test["identifier"] != missing + "()"]
    with pytest.raises(runner.RunnerError, match="missing from Xcode discovery"):
        runner.xcode_inventory(document, suite, selected)


@pytest.mark.parametrize("args", [["--smoke-shard", "0/3"], ["--smoke-shard", "4/3"],
    ["--smoke-shard", "1/999"], ["--smoke-shard", "invalid"],
    ["--suite", "ui-smoke", "--smoke-shard", "0/4"],
    ["--suite", "ui-smoke", "--smoke-shard", "5/4"],
    ["--suite", "full", "--smoke-shard", "1/3"], ["--suite", "native", "--smoke-shard", "1/3"],
    ["--suite", "core", "--smoke-shard", "1/3"]])
def test_invalid_shard_is_rejected_before_any_tool_runs(args):
    with pytest.raises(SystemExit) as stopped:
        runner.main(args)
    assert stopped.value.code == 2


@pytest.mark.parametrize("mutation", ["error", "empty", "disabled", "missing-smoke", "duplicate"])
@pytest.mark.parametrize("suite", ["smoke", "ui-smoke"])
def test_xcode_enumeration_fails_closed(mutation, suite):
    document = enumeration()
    if mutation == "error":
        document["errors"] = ["synthetic error"]
    elif mutation == "empty":
        document["values"] = []
    elif mutation == "disabled":
        document["values"][0]["disabledTests"] = [{"identifier": "disabled"}]
    elif mutation == "missing-smoke":
        document["values"][0]["enabledTests"].pop(1)
    else:
        document["values"][0]["enabledTests"] *= 2
    with pytest.raises(runner.RunnerError):
        runner.xcode_inventory(document, suite)



def test_swift_suite_teardown_failure_is_not_a_pass():
    expected, output = package_example()
    with pytest.raises(runner.RunnerError, match="suite failed"):
        runner.audit_package(output + "Test Suite 'CoreTests' failed at timestamp.\n", expected)


@pytest.mark.parametrize("argv,suite,shard", [([], "smoke", (1, 1)),
    (["--suite", "ui-smoke", "--smoke-shard", "2/4"], "ui-smoke", (2, 4))])
def test_main_retains_suite_and_shard_failure_summary_without_real_tools(tmp_path, monkeypatch, argv, suite, shard):
    from contextlib import nullcontext

    seed_sources(tmp_path)
    monkeypatch.setattr(runner, "__file__", str(tmp_path / "scripts/test_ios.py"))
    monkeypatch.setattr(runner.sys, "platform", "darwin")
    monkeypatch.setattr(runner, "runner_lock", lambda path: nullcontext(None))
    seen = []

    def failure(run, suite, report):
        seen.append(suite)
        raise runner.RunnerError("synthetic missing Xcode")

    monkeypatch.setattr(runner, "execute_suite", failure)
    assert runner.main(argv) == 1
    assert seen == [suite]
    artifacts = list((tmp_path / "output/ios-tests").glob("*/summary.json"))
    assert len(artifacts) == 1
    result = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["error"] == "synthetic missing Xcode"
    assert result["suite"] == suite
    assert result["smoke_tests"] == list(runner.smoke_partition(*shard))
    assert result["smoke_shard"] == {"index": shard[0], "count": shard[1]}


@pytest.mark.parametrize("unknown", [
    "RijiCoreTests.Suite/example()", "RijiCoreTests.Suite.Nested/testExample",
    "RijiCoreTests.testExample()", "OtherTests/Suite/example()",
    "RijiStateTests.Suite/test_with argument()", "OtherTests/testExample",
])
def test_test_shaped_unsupported_discovery_is_never_silently_dropped(unknown):
    expected, _ = package_example()
    with pytest.raises(runner.RunnerError, match="test-shaped"):
        runner.package_inventory("Building for debugging...\n" + "\n".join(expected) + "\n" + unknown)


def test_same_class_and_method_in_distinct_targets_are_audited_independently():
    expected = {target + ".SharedTests/testShared": target for target in runner.PACKAGE_TARGETS}
    assert runner.package_inventory("\n".join(expected)) == expected
    lines = ["Test Case '-[" + target + ".SharedTests testShared]' " + status + "."
             for target in runner.PACKAGE_TARGETS for status in ("started", "passed")]
    assert runner.audit_package("\n".join(lines), expected)["passed"] == 3
    with pytest.raises(runner.RunnerError, match="inventory differs"):
        runner.audit_package("\n".join(lines[2:]), expected)


def test_unknown_case_status_cannot_hide_beside_valid_events():
    expected, output = package_example()
    with pytest.raises(runner.RunnerError, match="Unrecognized Swift XCTest event"):
        runner.audit_package(output + "Test Case 'RijiCoreTests.Extra.testOther' ignored.\n", expected)


@pytest.mark.parametrize("app_bundle", [True, False])
def test_developer_directory_accepts_app_bundle_and_contents_form(tmp_path, monkeypatch, app_bundle):
    run = make_run(tmp_path)
    bundle = tmp_path / "Xcode.app"
    developer = bundle / "Contents/Developer"
    (developer / "Platforms/iPhoneSimulator.platform").mkdir(parents=True)
    run.environment["DEVELOPER_DIR"] = str(bundle if app_bundle else developer)
    monkeypatch.setattr(run, "command", lambda *args: "synthetic version")
    environment = runner.check_tools(run)
    assert environment["developer_directory"] == str(developer)
    assert run.environment["DEVELOPER_DIR"] == str(developer)


@pytest.mark.parametrize("ignore_term", [False, True])
def test_command_timeout_stops_only_its_owned_child_and_retains_evidence(tmp_path, monkeypatch, ignore_term):
    run = make_run(tmp_path)
    monkeypatch.setattr(runner, "COMMAND_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(runner, "CHILD_STOP_GRACE_SECONDS", 0.1)
    body = ("import signal,time; " + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
            + "print('synthetic timeout', flush=True); time.sleep(10)")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        started = time.monotonic()
        with pytest.raises(runner.RunnerTimeout):
            run.command("delay", [sys.executable, "-c", body])
        assert time.monotonic() - started < 3
        record = json.loads((run.artifacts / "commands.json").read_text())[0]
        assert record["status"] == "timed_out"
        assert record["cleanup"]["stopped"] and record["returncode"] != 0
        assert record["pid"] != unrelated.pid
        assert record["finished_at"]
        assert "synthetic timeout" in (run.artifacts / "delay.log").read_text()
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_whole_run_deadline_bounds_a_longer_stage(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    run.control.deadline = time.monotonic() + 0.15
    with pytest.raises(runner.RunnerTimeout, match="whole-run"):
        run.command("delay", [sys.executable, "-c", "import time; time.sleep(10)"])
    assert run.commands[-1]["status"] == "timed_out"
    assert run.commands[-1]["cleanup"]["stopped"]


@pytest.mark.parametrize("discovery", [
    ("swift-discovery", ["synthetic-swift", "test", "list"], 360),
    ("xcode-discovery", ["synthetic-xcodebuild", "test", "-enumerate-tests"], 660),
])
@pytest.mark.parametrize("whole_run_expires", [False, True])
def test_cold_discovery_budget_preserves_whole_run_deadline(
        tmp_path, monkeypatch, discovery, whole_run_expires):
    stage, args, build_seconds = discovery
    elapsed = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: elapsed[0])

    class ColdBuild:
        pid = 12345
        returncode = None

        def wait(self, timeout):
            if self.returncode is not None:
                return self.returncode
            elapsed[0] += timeout
            if elapsed[0] >= build_seconds:
                self.returncode = 0
                return 0
            raise subprocess.TimeoutExpired("synthetic cold build", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -signal.SIGTERM

    process = ColdBuild()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    run = make_run(tmp_path)
    if whole_run_expires:
        run.control.deadline = elapsed[0] + build_seconds - 30
        with pytest.raises(runner.RunnerTimeout, match="whole-run"):
            run.command(stage, args)
        assert elapsed[0] == build_seconds - 30
        assert run.commands[-1]["status"] == "timed_out"
        assert run.commands[-1]["cleanup"]["stopped"]
        assert run.commands[-1]["returncode"] == -signal.SIGTERM
    else:
        run.command(stage, args)
        assert elapsed[0] >= build_seconds
        assert run.commands[-1]["status"] == "passed"
        assert "cleanup" not in run.commands[-1]


def seed_sources(root):
    for name, text in {"scripts/test_ios.py": "# Synthetic runner\n",
                       "ios/Package.swift": "// Synthetic manifest\n",
                       "ios/Riji/Synthetic.swift": "// Synthetic source\n"}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_source_manifest_identifies_untracked_inputs_and_excludes_private_or_generated_files(tmp_path):
    seed_sources(tmp_path)
    before = runner.source_manifest(tmp_path)
    for name in ("ios/.env", "ios/.build/Cached.swift", "ios/Project/xcuserdata/Private.swift",
                 "ios/.swiftpm/settings.json", "ios/output/Generated.swift",
                 "ios/Signing.local.xcconfig", "ios/Config.private.xcconfig",
                 "ios/secrets/Signing.xcconfig"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic excluded value")
    assert runner.source_manifest(tmp_path) == before
    assert set(before) == {"scripts/test_ios.py", "ios/Package.swift", "ios/Riji/Synthetic.swift"}
    assert all(len(value) == 64 for value in before.values())


@pytest.mark.parametrize("mutation", [None, "modify", "add", "remove", "fixture-add", "fixture-modify", "fixture-remove"])
def test_source_evidence_compares_both_snapshots(tmp_path, mutation):
    seed_sources(tmp_path)
    run = make_run(tmp_path)
    run.root = tmp_path
    report = {}
    fixture = tmp_path / "tests/fixtures/mobile-ai-result.md"
    fixture.parent.mkdir(parents=True)
    if mutation in {"fixture-modify", "fixture-remove"}:
        fixture.write_text("Synthetic AI provenance fixture")

    def execute():
        with runner.verified_sources(run, report):
            target = tmp_path / "ios/Riji/Synthetic.swift"
            if mutation == "modify":
                target.write_text("// changed")
            elif mutation == "add":
                target.with_name("Added.swift").write_text("// added")
            elif mutation == "remove":
                target.unlink()
            elif mutation in {"fixture-add", "fixture-modify"}:
                fixture.write_text("Changed synthetic fixture")
            elif mutation == "fixture-remove":
                fixture.unlink()

    if mutation:
        with pytest.raises(runner.RunnerError, match="Source inputs changed"):
            execute()
        assert report["source_inputs"]["status"] == "changed"
    else:
        execute()
        assert report["source_inputs"]["status"] == "unchanged"
    assert (run.artifacts / "source-inputs-before.json").is_file()
    assert (run.artifacts / "source-inputs-after.json").is_file()


def load_runner_program():
    return ("import importlib.util,sys; from pathlib import Path; "
            "spec=importlib.util.spec_from_file_location('synthetic_runner',sys.argv[1]); "
            "r=importlib.util.module_from_spec(spec); sys.modules[spec.name]=r; spec.loader.exec_module(r); "
            "base=Path(sys.argv[2]); ")


def wait_for_file(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    pytest.fail("Synthetic child did not become ready: " + path.name)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal delivery")
@pytest.mark.parametrize("cause", ["signal", "timeout"])
def test_interruption_finishes_main_and_active_command_evidence(tmp_path, cause):
    seed_sources(tmp_path)
    child = "import time; from pathlib import Path; Path(__import__('sys').argv[1]).touch(); time.sleep(10)"
    program = load_runner_program() + "\nr.__file__=str(base/'scripts/test_ios.py')\nr.sys.platform='darwin'\nr.Path.home=classmethod(lambda cls: base)\n"
    if cause == "timeout":
        program += "r.COMMAND_TIMEOUT_SECONDS=0.2\n"
    program += ("def synthetic(run,suite,report):\n"
                "    run.command('signal-child',[sys.executable,'-c'," + repr(child) + ",str(base/'ready')])\n"
                "r.execute_suite=synthetic\nraise SystemExit(r.main(['--suite','core']))\n")
    owner = subprocess.Popen([sys.executable, "-c", program, str(ROOT / "scripts/test_ios.py"), str(tmp_path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_file(tmp_path / "ready")
        if cause == "signal":
            owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout=5) == 1
        evidence = next((tmp_path / "output/ios-tests").iterdir())
        summary = json.loads((evidence / "summary.json").read_text())
        command = json.loads((evidence / "commands.json").read_text())[0]
        assert summary["status"] == "failed"
        assert ("SIGTERM" if cause == "signal" else "deadline") in summary["error"]
        assert summary["source_inputs"]["status"] == "unchanged"
        assert command["status"] == ("interrupted" if cause == "signal" else "timed_out")
        assert command["cleanup"]["stopped"]
        assert command["returncode"] != 0 and command["finished_at"]
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited flock descriptors")
def test_child_keeps_runner_lock_after_owner_dies_until_child_exits(tmp_path):
    child = ("import sys,time; from pathlib import Path; base=Path(sys.argv[1]); "
             "(base/'ready').touch(); deadline=time.monotonic()+8\n"
             "while not (base/'release').exists() and time.monotonic()<deadline: time.sleep(0.02)\n"
             "(base/'done').touch()\n")
    owner_code = load_runner_program() + ("\nwith r.runner_lock(base/'runner.lock') as fd:\n"
        "    run=r.Run(base,base,base,fd)\n"
        "    run.command('lock-child',[sys.executable,'-c'," + repr(child) + ",str(base)])\n")
    competitor_code = load_runner_program() + ("\ntry:\n"
        "    with r.runner_lock(base/'runner.lock'): pass\n"
        "except r.RunnerError: raise SystemExit(23)\n")
    args = [str(ROOT / "scripts/test_ios.py"), str(tmp_path)]
    owner = subprocess.Popen([sys.executable, "-c", owner_code, *args],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def compete():
        return subprocess.run([sys.executable, "-c", competitor_code, *args], timeout=3).returncode

    try:
        wait_for_file(tmp_path / "ready")
        owner.kill()
        owner.wait(timeout=3)
        assert compete() == 23
        (tmp_path / "release").touch()
        wait_for_file(tmp_path / "done")
        deadline = time.monotonic() + 3
        while compete() != 0:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        (tmp_path / "release").touch()
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)
