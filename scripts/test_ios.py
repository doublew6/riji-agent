#!/usr/bin/env python3
"""Run isolated, audited iOS regression tests using only the Python stdlib."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import signal
import subprocess
import sys
import time
from typing import Any, Iterator, Optional
from uuid import uuid4


COMMAND_TIMEOUT_SECONDS = 120
RUN_TIMEOUT_SECONDS = 50 * 60
CHILD_STOP_GRACE_SECONDS = 5
STAGE_TIMEOUT_SECONDS = {
    # Initial boot includes data migration on a fresh CI device.
    "boot-status": 300,
    "ui-boot-status": 180,
    # Listing tests also compiles the package on a fresh CI runner.
    "swift-discovery": 15 * 60,
    "swift-tests": 15 * 60,
    # Enumeration also builds and loads the test bundles on a cold CI runner.
    "xcode-discovery": 15 * 60,
    "xcode-tests": 40 * 60,
}

DEVICE_NAME = "Riji Automated Tests"
PACKAGE_TARGETS = {"RijiCoreTests", "RijiStateTests", "RijiICloudProbeSupportTests"}
SMOKE_METHODS = (
    "testOrdinaryBodyConflictFragmentChoiceEditRecoveryAndSaveExactBytes",
    "testSettingsExportsTheExactCurrentMarkdownThroughSystemFiles",
    "testApprovedSettingsGroupsKeepDiaryAndRecoveryRoutesReachable",
    "testApprovedMemoryFiltersAndUnavailableReportWithDarkLargeText",
    "testApprovedDirectionHierarchyKeepsGoalVersionsAndMITConfirmation",
    "testOriginalHomeManualEditingSavesAndColdReopensExactSource",
    "testOriginalDiaryConfirmationAndAutosaveKeepScrolledWritingPosition",
    "testNavigationGesturesAndDestinationRoundTripsPreserveActualReadingPosition",
    "testBothSwipesPreviewMissingFutureOriginalDaysAndReturnWithoutCreatingFiles",
    "testMissingFutureDayFirstEditSavesAndColdReopensWithoutChangingSource",
    "testFailedSaveAndEditingGestureCannotLeaveDiaryOrLoseInput",
    "testOriginalTableHorizontalAndVerticalGesturesStayInSameDiary",
    "testInlineTableReturnAddRowAddColumnAndColdReopenKeepExactSource",
    "testActiveExternalFileRefreshAllowsContinuedEditingAndColdReopen",
    "testCompetingExternalEditsOfferLatestAndLocalChoicesWithoutReauthorization",
    "testHomeAndActivatePreservePendingBodyAndTableInput",
    "testCloudConflictColdOpenDirectoryChoiceAndBackupRemainReachable",
    "testCloudManualMergeClosesAndColdReopensWithoutChangingCurrentFile",
    "testCloudDifferencesShowOnlyChangesAndPerHunkChoicesPersistThenConfirm",
    "testCloudDifferenceFragmentEditingKeepsOtherParagraphsAndColdReopensExactResult",
    "testWholeCurrentDiaryChoiceNeedsNoPerFragmentDecisionsAndResumesEditing",
    "testUnselectedOriginalHomeShowsFolderActionAndFourMainTabsWork",
    "testSettingsMainTabSavesInputAndKeepsSubpagesAndRecordingsReachable",
    "testMobileManagedInstallationConnectsWithoutLeavingFourPeerTabs",
    "testMobileMentorDraftColdRecoveryReplyAndMemoryCorrection",
    "testMentorNorthStarOriginalParagraphPersistsAcrossChatsAndSourceSwitchClearsIt",
    "testMentorAccessibilityTextKeyboardAndRoundtableControlsRemainReachable",
    "testMentorNorthStarExternalChangeInvalidatesOpenChat",
    "testMobileGroupsEmptyCreationRenameIndependentDraftsAndColdReturn",
    "testMobileGroupNameEditorCancelsWithoutChangingNameAndDisablesUnchangedSave",
    "testMobileGroupMessageSelectionOnlyAppearsAfterExplicitActionAndCanCancel",
    "testMobilePrivateAndGroupSendKeepInputFocusForFollowingDraft",
    "testMobileGroupMentionAndLargeTextKeepInputReachable",
    "testMobileGroupResultPreviewRequiresConfirmationAndKeepsExactSelection",
    "testMobileRoundtableHostInvitationAndLocalSavePreview",
    "testMobileRoundtableStoppedSummaryAndColdOfflineDraft",
    "testMobileMemoryOverviewThemeEvidenceCorrectionAndInvalidation",
    "testMobileCorrectionConfirmationRejectsRemoteChangeAndRetainsInput",
    "testMobileOlderHistoryKeepsCurrentPageAndDraft",
    "testMobileCorrectionRequiresVisibleComparisonAfterRemoteChange",
    "testMobileCorruptArchiveWarningPreservesHealthyContentAndNeverClaimsEmpty",
)
SMOKE_TESTS = tuple("RijiUITests/DiaryFlowTests/" + name for name in SMOKE_METHODS)
SMOKE_SUITES = ("smoke", "ui-smoke")


def smoke_partition(index: int, count: int) -> tuple[str, ...]:
    if not 1 <= index <= count <= len(SMOKE_TESTS):
        raise RunnerError("Smoke shard must satisfy 1 <= index <= count <= smoke test count.")
    return SMOKE_TESTS if count == 1 else tuple(sorted(SMOKE_TESTS)[index - 1::count])


class RunnerError(RuntimeError):
    """An unmet prerequisite, failed command, or incomplete test result."""


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def runtime_version(value: str) -> int:
    parts = [int(part) for part in value.split(".")]
    parts += [0] * (3 - len(parts))
    return (parts[0] << 16) | (parts[1] << 8) | parts[2]


def compatible_type(runtime: dict, device_type: dict) -> bool:
    minimum, maximum = (device_type.get(key) for key in ("minRuntimeVersion", "maxRuntimeVersion"))
    return (device_type.get("name", "").startswith("iPhone")
            and isinstance(minimum, int) and isinstance(maximum, int)
            and minimum <= runtime_version(runtime["version"]) <= maximum)


def select_device(devices: dict, runtimes: dict, types: dict) -> dict:
    """Inspect unavailable devices too; never replace an existing named device."""
    available = {item["identifier"]: item for item in runtimes["runtimes"]
                 if item.get("isAvailable") and ".iOS-" in item["identifier"]}
    existing = [(runtime, device) for runtime, group in devices["devices"].items()
                for device in group if device["name"] == DEVICE_NAME]
    if len(existing) > 1:
        raise RunnerError("Multiple Riji Automated Tests devices exist. Resolve duplicate names manually.")
    if existing:
        runtime, device = existing[0]
        if not device.get("isAvailable") or runtime not in available:
            raise RunnerError("Existing Riji Automated Tests is unavailable. Restore its runtime in Xcode.")
        matching = [item for item in types["devicetypes"]
                    if item["identifier"] == device.get("deviceTypeIdentifier")]
        if not matching or not compatible_type(available[runtime], matching[0]):
            raise RunnerError("Existing automated device is not a compatible iPhone. Inspect it manually.")
        if device["state"] not in {"Booted", "Shutdown"}:
            raise RunnerError("Automated device is transitioning. Wait for it to settle and rerun.")
        return {"device": device, "runtime": available[runtime], "type": matching[0], "created": False}
    if not available:
        raise RunnerError("No available iOS Simulator runtime. Install one in Xcode Settings > Components.")
    for runtime in sorted(available.values(), key=lambda item: runtime_version(item["version"]), reverse=True):
        compatible = [item for item in types["devicetypes"] if compatible_type(runtime, item)]
        if compatible:
            device_type = max(compatible, key=lambda item: (item["minRuntimeVersion"], item["identifier"]))
            return {"runtime": runtime, "type": device_type, "created": True}
    raise RunnerError("No compatible iPhone/runtime pair. Install matching Xcode Simulator components.")


@contextmanager
def runner_lock(path: Path) -> Iterator[int]:
    # Imported here so importing the module and fake-tool tests work on Windows.
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RunnerError("Another iOS runner owns the lock. Wait for it; do not remove the lock file.") from error
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()) + "\n")
        handle.flush()
        # Child tools retain this lock if the parent unexpectedly exits.
        yield handle.fileno()
    # Keep the same inode. Removing it would allow two owners during a race.


class RunnerInterrupted(RunnerError):
    """A controlled signal requested that the current run stop."""


class RunnerTimeout(RunnerError):
    """An internal command or whole-run deadline expired."""


@dataclass
class ExecutionControl:
    deadline: float = field(default_factory=lambda: time.monotonic() + RUN_TIMEOUT_SECONDS)
    signal_number: Optional[int] = None

    def check(self) -> None:
        if self.signal_number is not None:
            raise RunnerInterrupted("Interrupted by " + signal.Signals(self.signal_number).name + ".")
        if time.monotonic() >= self.deadline:
            raise RunnerTimeout("The 50-minute whole-run deadline expired.")


@contextmanager
def controlled_signals(control: ExecutionControl) -> Iterator[None]:
    def request_stop(number: int, frame: Any) -> None:
        if control.signal_number is None:
            control.signal_number = number

    previous = {number: signal.signal(number, request_stop) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def stop_child(process: subprocess.Popen) -> dict:
    """Signal only this still-owned direct child, never a process group or service."""
    actions = []
    for action in ("terminate", "kill"):
        if process.poll() is not None:
            break
        getattr(process, action)()
        actions.append(action)
        try:
            process.wait(timeout=CHILD_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            continue
    return {"actions": actions, "returncode": process.poll(), "stopped": process.poll() is not None}


def wait_child(process: subprocess.Popen, control: ExecutionControl, seconds: float) -> int:
    deadline = time.monotonic() + seconds
    while True:
        control.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RunnerTimeout("The command stage deadline expired.")
        try:
            return process.wait(timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            continue


@dataclass
class Run:
    root: Path
    artifacts: Path
    cache: Path
    lock_fd: Optional[int] = None
    environment: dict = field(default_factory=lambda: {
        key: value for key, value in os.environ.items()
        if not key.startswith(("RIJI_", "SIMCTL_CHILD_"))
    })
    commands: list = field(default_factory=list)
    control: ExecutionControl = field(default_factory=ExecutionControl)
    smoke_tests: tuple[str, ...] = SMOKE_TESTS

    def command(self, name: str, args: list[str], check: bool = True) -> str:
        self.control.check()
        log = self.artifacts / (name + ".log")
        seconds = STAGE_TIMEOUT_SECONDS.get(name, COMMAND_TIMEOUT_SECONDS)
        record = {"name": name, "args": args, "log": log.name, "status": "starting",
                  "timeout_seconds": seconds, "returncode": None, "started_at": datetime.now(timezone.utc).isoformat()}
        self.commands.append(record)
        write_json(self.artifacts / "commands.json", self.commands)
        print("Running " + name + " (" + log.name + ")", flush=True)
        options = {"pass_fds": (self.lock_fd,)} if self.lock_fd is not None else {}
        process = None
        try:
            with log.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(args, cwd=self.root, env=self.environment,
                                           stdout=output, stderr=subprocess.STDOUT, **options)
                record.update(pid=process.pid, status="running")
                write_json(self.artifacts / "commands.json", self.commands)
                code = wait_child(process, self.control, seconds)
            record.update(returncode=code, status="passed" if code == 0 else "failed")
        except (Exception, KeyboardInterrupt) as error:
            record.update(status="timed_out" if isinstance(error, RunnerTimeout) else "failed",
                          error=str(error) or "KeyboardInterrupt")
            if isinstance(error, (RunnerInterrupted, KeyboardInterrupt)):
                record["status"] = "interrupted"
            if process is not None:
                record["cleanup"] = stop_child(process)
                record["returncode"] = process.returncode
            raise
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_json(self.artifacts / "commands.json", self.commands)
        if check and record["returncode"]:
            raise RunnerError(name + " failed (exit " + str(record["returncode"]) + "). See " + log.name)
        return log.read_text(encoding="utf-8", errors="replace")

    def json_command(self, name: str, args: list[str]) -> dict:
        value = json.loads(self.command(name, args))
        write_json(self.artifacts / (name + ".json"), value)
        return value


def check_tools(run: Run) -> dict:
    developer = run.environment.get("DEVELOPER_DIR") or run.command("developer-directory", ["xcode-select", "-p"]).strip()
    developer_path = Path(developer)
    if developer_path.suffix == ".app" and developer_path.is_dir():
        developer_path = developer_path / "Contents/Developer"
    developer = str(developer_path)
    if not (developer_path / "Platforms/iPhoneSimulator.platform").is_dir():
        raise RunnerError("Full Xcode is required. Set DEVELOPER_DIR or select Xcode in Settings > Locations.")
    run.environment["DEVELOPER_DIR"] = developer
    xcode = run.command("xcode-version", ["xcodebuild", "-version"]).strip()
    swift = run.command("swift-version", ["xcrun", "swift", "--version"]).strip()
    return {"developer_directory": developer, "xcode": xcode, "swift": swift,
            "macos": platform.mac_ver()[0], "architecture": platform.machine(),
            "python": platform.python_version()}


def prepare_device(run: Run) -> dict:
    inventory = {name: run.json_command(name, ["xcrun", "simctl", "list", name, "-j"])
                 for name in ("devices", "runtimes", "devicetypes")}
    choice = select_device(inventory["devices"], inventory["runtimes"], inventory["devicetypes"])
    if choice["created"]:
        udid = run.command("create-device", ["xcrun", "simctl", "create", DEVICE_NAME,
                           choice["type"]["identifier"], choice["runtime"]["identifier"]]).strip()
        if not re.fullmatch(r"[0-9A-Fa-f-]{36}", udid):
            raise RunnerError("simctl create did not return a device UUID. Inspect create-device.log.")
        choice["device"] = {"udid": udid, "name": DEVICE_NAME, "state": "Shutdown"}
    write_json(run.artifacts / "selected-device.json", choice)
    device = choice["device"]
    if device["state"] == "Shutdown":
        run.command("boot-device", ["xcrun", "simctl", "boot", device["udid"]])
    run.command("boot-status", ["xcrun", "simctl", "bootstatus", device["udid"], "-b"])
    return choice


def package_inventory(output: str) -> dict:
    tests = {}
    for raw in output.splitlines():
        line = raw.strip()
        match = re.fullmatch(r"(\w+)\.(\w+)/(test\w+)", line)
        if match is None:
            if (any(line.startswith(target + ".") for target in PACKAGE_TARGETS)
                    or re.match(r"^\w+(?:\.\S+)?/\S|^\w+\.\S*\(", line)):
                raise RunnerError("Unrecognized Swift test-shaped discovery line: " + line)
            continue
        target, class_name, method = match.groups()
        key = target + "." + class_name + "/" + method
        if key in tests:
            raise RunnerError("Duplicate Swift test identifier: " + key)
        tests[key] = target
    if set(tests.values()) != PACKAGE_TARGETS:
        raise RunnerError("Swift discovery did not include all Core, State and Probe support test targets.")
    return tests


def package_events(output: str) -> list[tuple[str, str]]:
    events = []
    for line in output.splitlines():
        if not line.startswith("Test Case "):
            continue
        event = re.match(r"^Test Case '([^']+)' (started|passed|failed|skipped)\b", line)
        if event is None:
            raise RunnerError("Unrecognized Swift XCTest event: " + line)
        name, status = event.groups()
        match = re.fullmatch(r"-\[(\w+)\.(\w+) (test\w+)\]", name)
        if match is None:
            match = re.fullmatch(r"(\w+)\.(\w+)\.(test\w+)", name)
        if match is None:
            raise RunnerError("Unrecognized Swift XCTest case identifier: " + name)
        target, class_name, method = match.groups()
        events.append((target + "." + class_name + "/" + method, status))
    return events


def audit_package(output: str, expected: dict) -> dict:
    # XCTest uses Objective-C names on Xcode 27 and dotted names on older tools.
    if re.search(r"^Test Suite .* (failed|skipped)\b", output, re.MULTILINE):
        raise RunnerError("Swift test suite failed or skipped.")
    events = package_events(output)
    for key, status in events:
        if key not in expected:
            raise RunnerError("Swift executed an undiscovered test: " + key)
        if status in {"failed", "skipped"}:
            raise RunnerError("Swift test failed or skipped: " + key)
    started = Counter(key for key, status in events if status == "started")
    passed = Counter(key for key, status in events if status == "passed")
    if not expected or started != Counter(expected.keys()) or passed != started:
        raise RunnerError("Swift result inventory differs from discovered tests, or no tests executed.")
    return {"passed": len(passed), "failed": 0, "skipped": 0,
            "targets": dict(Counter(expected.values())), "tests": sorted(expected)}


def execute_package(run: Run) -> dict:
    args = ["xcrun", "swift", "test", "--package-path", str(run.root / "ios"),
            "--scratch-path", str(run.cache / "swift"), "--disable-netrc", "--disable-keychain"]
    expected = package_inventory(run.command("swift-discovery", args + ["list"]))
    write_json(run.artifacts / "swift-inventory.json", expected)
    output = run.command("swift-tests", args + ["--no-parallel"])
    result = audit_package(output, expected)
    write_json(run.artifacts / "swift-results.json", result)
    return result


def result_cases(nodes: list, bundle: str = "") -> list[tuple[str, str]]:
    cases = []
    for node in nodes:
        if node.get("nodeType") in {"Unit test bundle", "UI test bundle"}:
            bundle = node["name"]
        if node.get("nodeType") == "Test Case":
            cases.append((bundle + "/" + node["nodeIdentifier"].removesuffix("()"), node.get("result", "")))
        else:
            cases.extend(result_cases(node.get("children", []), bundle))
    return cases


def audit_xcode(summary: dict, tests: dict, suite: str, udid: str, expected: set[str]) -> dict:
    cases = result_cases(tests.get("testNodes", []))
    if not cases or any(result != "Passed" for _, result in cases):
        raise RunnerError("Xcode has failed, skipped, unknown, or zero executed test cases.")
    for field_name in ("failedTests", "skippedTests", "expectedFailures"):
        if summary.get(field_name) != 0:
            raise RunnerError("Xcode result has nonzero or missing " + field_name + ".")
    if (summary.get("result") != "Passed" or summary.get("passedTests") != len(cases)
            or summary.get("totalTestCount") != len(cases)):
        raise RunnerError("Xcode summary and executed case counts disagree.")
    if not tests.get("devices") or any(device.get("deviceId") != udid for device in tests["devices"]):
        raise RunnerError("Xcode results used an unexpected device or a Simulator clone.")
    identifiers = [identifier for identifier, _ in cases]
    if len(set(identifiers)) != len(identifiers):
        raise RunnerError("Xcode returned duplicate test executions.")
    counts = Counter(identifier.split("/")[0] for identifier in identifiers)
    expected_targets = {"RijiNativeTests"} if suite == "native" else {"RijiNativeTests", "RijiUITests"}
    if set(counts) != expected_targets:
        raise RunnerError("Xcode did not execute the requested test bundles.")
    if suite in SMOKE_SUITES:
        requested_ui = {item for item in expected if item.startswith("RijiUITests/")}
        actual_ui = {item for item in identifiers if item.startswith("RijiUITests/")}
        if not requested_ui or not requested_ui.issubset(SMOKE_TESTS) or actual_ui != requested_ui:
            raise RunnerError("Requested smoke tests are missing or unexpected UI tests executed.")
    if set(identifiers) != expected:
        raise RunnerError("Xcode executed inventory differs from the complete requested inventory.")
    return {"passed": len(cases), "failed": 0, "skipped": 0,
            "targets": dict(counts), "tests": sorted(identifiers)}


def xcode_arguments(run: Run, suite: str, udid: str) -> list[str]:
    args = ["xcodebuild", "-project", str(run.root / "ios/Riji.xcodeproj"), "-scheme", "Riji",
            "-configuration", "Debug", "-destination", "platform=iOS Simulator,id=" + udid,
            "-derivedDataPath", str(run.cache / "xcode"),
            "-resultBundlePath", str(run.artifacts / "tests.xcresult"),
            "-parallel-testing-enabled", "NO", "-maximum-concurrent-test-simulator-destinations", "1",
            "-maximum-parallel-testing-workers", "1", "-disableAutomaticPackageResolution",
            "-collect-test-diagnostics", "never",
            "CODE_SIGNING_ALLOWED=NO", "test", "-only-testing:RijiNativeTests"]
    if suite == "full":
        args.append("-only-testing:RijiUITests")
    elif suite in SMOKE_SUITES:
        args.extend("-only-testing:" + test for test in run.smoke_tests)
    return args


def xcode_inventory(document: dict, suite: str, smoke_tests: tuple[str, ...] = SMOKE_TESTS) -> set[str]:
    if document.get("errors") or not document.get("values"):
        raise RunnerError("Xcode test enumeration failed or returned no test plans.")
    identifiers = []
    for plan in document["values"]:
        if plan.get("disabledTests"):
            raise RunnerError("Xcode test enumeration contains disabled tests.")
        identifiers.extend(test["identifier"].removesuffix("()") for test in plan["enabledTests"])
    if len(set(identifiers)) != len(identifiers):
        raise RunnerError("Xcode test enumeration contains duplicate identifiers.")
    targets = {test.split("/")[0] for test in identifiers}
    required = {"RijiNativeTests"} if suite == "native" else {"RijiNativeTests", "RijiUITests"}
    if targets != required:
        raise RunnerError("Xcode discovery did not include the requested full test bundles.")
    if suite in SMOKE_SUITES and not set(SMOKE_TESTS).issubset(identifiers):
        raise RunnerError("Requested smoke tests are missing from Xcode discovery.")
    if suite in SMOKE_SUITES and (not smoke_tests or not set(smoke_tests).issubset(SMOKE_TESTS)
                                 or len(set(smoke_tests)) != len(smoke_tests)):
        raise RunnerError("Invalid smoke shard selection.")
    return {test for test in identifiers if test.startswith("RijiNativeTests/")
            or suite == "full" or (suite in SMOKE_SUITES and test in smoke_tests)}


def enumerate_xcode(run: Run, suite: str, udid: str) -> set[str]:
    # Enumeration loads test bundles on the dedicated device; it is not a static read.
    args = xcode_arguments(run, "native" if suite == "native" else "full", udid)
    args[args.index("-resultBundlePath") + 1] = str(run.artifacts / "enumeration.xcresult")
    path = run.artifacts / "xcode-inventory.json"
    run.command("xcode-discovery", args + ["-enumerate-tests", "-test-enumeration-style", "flat",
                "-test-enumeration-format", "json", "-test-enumeration-output-path", str(path)])
    expected = xcode_inventory(json.loads(path.read_text(encoding="utf-8")), suite, run.smoke_tests)
    write_json(run.artifacts / "xcode-requested.json", sorted(expected))
    return expected


UI_BUNDLES = (
    ("Riji.app", "com.riji.local", ("Riji",)),
    ("RijiUITests-Runner.app", "com.riji.local.uitests.xctrunner",
     ("RijiUITests-Runner", "PlugIns/RijiUITests.xctest/RijiUITests")),
)


def owned_ui_device(run: Run, choice: dict, phase: str) -> dict:
    """Revalidate the locked, dedicated device before any UI refresh operation."""
    lock = run.cache / "runner.lock"
    try:
        held, current = os.fstat(run.lock_fd), lock.stat()
        locked = ((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)
                  and lock.read_text(encoding="utf-8").strip() == str(os.getpid()))
    except (OSError, TypeError):
        locked = False
    if not locked:
        raise RunnerError("UI binary refresh requires the current runner lock.")
    udid = choice["device"]["udid"]
    inventory = run.json_command("ui-device-" + phase, ["xcrun", "simctl", "list", "devices", "-j"])
    matches = [(runtime, item) for runtime, group in inventory["devices"].items()
               for item in group if item.get("name") == DEVICE_NAME or item.get("udid") == udid]
    if len(matches) != 1:
        raise RunnerError("UI binary refresh requires one unchanged dedicated device.")
    runtime, device = matches[0]
    if (device.get("udid") != udid or device.get("name") != DEVICE_NAME
            or runtime != choice["runtime"]["identifier"]
            or device.get("deviceTypeIdentifier") != choice["type"]["identifier"]
            or not device.get("isAvailable") or device.get("state") != "Booted"):
        raise RunnerError("Dedicated UI device identity or availability changed after enumeration.")
    data = Path(device.get("dataPath", ""))
    if not data.is_absolute() or data.name != "data" or data.parent.name != udid:
        raise RunnerError("Dedicated UI device has no verified data directory.")
    return device


def ui_bundle_hashes(bundle: Path, specification: tuple) -> dict[str, str]:
    """Capture the app/runner launchers and code, including Debug dylibs if present."""
    _, identifier, executables = specification
    try:
        info = plistlib.loads((bundle / "Info.plist").read_bytes())
        if bundle.is_symlink() or info.get("CFBundleIdentifier") != identifier:
            raise RunnerError("UI binary bundle identity does not match the expected product.")
        if info.get("CFBundleExecutable") != executables[0]:
            raise RunnerError("UI binary bundle executable does not match the expected product.")
        paths = [bundle / item for item in executables]
        paths += [path.with_name(path.name + ".debug.dylib") for path in paths
                  if path.with_name(path.name + ".debug.dylib").exists()]
        hashes = {}
        for path in paths:
            path.resolve(strict=True).relative_to(bundle.resolve(strict=True))
            hashes[path.relative_to(bundle).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise RunnerError("UI binary bundle is missing or cannot be verified.") from error


def ui_binary_receipt(run: Run, choice: dict, phase: str) -> dict:
    device = owned_ui_device(run, choice, phase)
    products = run.cache / "xcode/Build/Products/Debug-iphonesimulator"
    containers = Path(device["dataPath"]).resolve() / "Containers/Bundle/Application"
    receipts = {}
    for specification in UI_BUNDLES:
        name, identifier, _ = specification
        built = ui_bundle_hashes(products / name, specification)
        raw = run.command("ui-container-" + phase + "-" + name,
                          ["xcrun", "simctl", "get_app_container", device["udid"], identifier, "app"])
        installed = Path(raw.strip())
        if (len(raw.strip().splitlines()) != 1 or not installed.is_absolute()
                or installed.name != name or installed.resolve().parent.parent != containers):
            raise RunnerError("Installed UI binary is outside the selected device application containers.")
        actual = ui_bundle_hashes(installed, specification)
        receipts[identifier] = {"built": built, "installed": actual, "matches": built == actual}
    write_json(run.artifacts / ("ui-binaries-" + phase + ".json"), receipts)
    if not all(item["matches"] for item in receipts.values()):
        raise RunnerError("Installed UI binary hashes do not match this run's built products.")
    return receipts


def refresh_ui_binaries(run: Run, choice: dict) -> dict:
    """Release runners loaded by enumeration without erasing synthetic test data."""
    device = owned_ui_device(run, choice, "refresh")
    products = run.cache / "xcode/Build/Products/Debug-iphonesimulator"
    for specification in UI_BUNDLES:
        ui_bundle_hashes(products / specification[0], specification)
    udid = device["udid"]
    run.command("ui-device-shutdown", ["xcrun", "simctl", "shutdown", udid])
    run.command("ui-device-boot", ["xcrun", "simctl", "boot", udid])
    run.command("ui-boot-status", ["xcrun", "simctl", "bootstatus", udid, "-b"])
    for name, _, _ in UI_BUNDLES:
        run.command("ui-install-" + name, ["xcrun", "simctl", "install", udid, str(products / name)])
    return ui_binary_receipt(run, choice, "before")


def execute_xcode(run: Run, suite: str, device: dict) -> dict:
    udid = device["device"]["udid"]
    expected = enumerate_xcode(run, suite, udid)
    binaries = refresh_ui_binaries(run, device) if suite != "native" else None
    run.command("xcode-tests", xcode_arguments(run, suite, udid), check=False)
    returncode = run.commands[-1]["returncode"]
    bundle = run.artifacts / "tests.xcresult"
    if not bundle.exists():
        raise RunnerError("Xcode produced no result bundle (exit " + str(returncode) + "). See xcode-tests.log.")
    args = ["xcrun", "xcresulttool", "get", "test-results"]
    summary = run.json_command("xcode-summary", args + ["summary", "--path", str(bundle), "--compact"])
    tests = run.json_command("xcode-cases", args + ["tests", "--path", str(bundle), "--compact"])
    if returncode:
        raise RunnerError("xcode-tests failed (exit " + str(returncode) + "); result evidence retained.")
    if binaries is not None and ui_binary_receipt(run, device, "after") != binaries:
        raise RunnerError("UI binary products changed during test execution; results are not accepted.")
    return audit_xcode(summary, tests, suite, udid, expected)


def execute_suite(run: Run, suite: str, report: dict) -> None:
    report["environment"] = check_tools(run)
    if suite != "core":
        report["device"] = prepare_device(run)
    if suite in {"core", "smoke", "full"}:
        report["package"] = execute_package(run)
        write_json(run.artifacts / "summary.json", report)
    if suite != "core":
        report["xcode"] = execute_xcode(run, suite, report["device"])


def source_manifest(root: Path) -> dict[str, str]:
    """Hash code, shared build settings, assets and the shared AI provenance fixture."""
    paths = [root / "scripts/test_ios.py"]
    fixture = root / "tests/fixtures/mobile-ai-result.md"
    if fixture.exists() or fixture.is_symlink():
        paths.append(fixture)
    ignored = {"build", "deriveddata", "output", "xcuserdata", "__pycache__",
               "private", "secrets", "credentials"}
    extensions = {".swift", ".pbxproj", ".xcscheme", ".xcconfig", ".entitlements"}
    for directory, names, files in os.walk(root / "ios", followlinks=False):
        names[:] = sorted(name for name in names if not name.startswith(".") and name.lower() not in ignored)
        for name in names:
            if (Path(directory) / name).is_symlink():
                raise RunnerError("Source manifest refuses a symlink directory under ios.")
        for name in sorted(files):
            path = Path(directory) / name
            asset = any(part.endswith(".xcassets") for part in path.parts)
            if name.startswith(".") or any(marker in name.lower() for marker in (".local.", ".private.")):
                continue
            if (path.suffix in extensions or name in {"Info.plist", "Package.resolved"}
                    or (asset and path.suffix in {".json", ".png", ".jpg", ".jpeg", ".pdf"})):
                paths.append(path)
    result = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise RunnerError("Source manifest requires regular input files.")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@contextmanager
def verified_sources(run: Run, report: dict) -> Iterator[None]:
    before = source_manifest(run.root)
    write_json(run.artifacts / "source-inputs-before.json", before)
    report["source_inputs"] = {"status": "checking", "files": len(before)}
    try:
        yield
    finally:
        after = source_manifest(run.root)
        write_json(run.artifacts / "source-inputs-after.json", after)
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        report["source_inputs"].update(status="changed" if changed else "unchanged", changed=changed)
        if changed:
            raise RunnerError("Source inputs changed during execution; see source-inputs manifests.")


def parse_arguments(argv: Optional[list[str]]) -> tuple[argparse.Namespace, tuple[int, int], tuple[str, ...]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("core", "native", "smoke", "ui-smoke", "full"), default="smoke")
    parser.add_argument("--smoke-shard", metavar="INDEX/COUNT",
                        help="Partition smoke or ui-smoke UI tests; Native stays complete, package runs only in smoke.")
    args = parser.parse_args(argv)
    shard = (1, 1)
    if args.smoke_shard is not None:
        if args.suite not in SMOKE_SUITES or not re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", args.smoke_shard):
            parser.error("--smoke-shard requires --suite smoke or ui-smoke and INDEX/COUNT.")
        index, count = map(int, args.smoke_shard.split("/"))
        shard = (index, count)
    try:
        smoke_tests = smoke_partition(*shard)
    except RunnerError as error:
        parser.error(str(error))
    return args, shard, smoke_tests


def main(argv: Optional[list[str]] = None) -> int:
    args, shard, smoke_tests = parse_arguments(argv)
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts = root / "output/ios-tests" / (stamp + "-" + args.suite + "-" + uuid4().hex[:8])
    artifacts.mkdir(parents=True)
    cache = Path.home() / "Library/Caches/riji-ios-automated-tests"
    report = {"suite": args.suite, "status": "running", "started_at": stamp,
              "smoke_tests": list(smoke_tests) if args.suite in SMOKE_SUITES else [],
              "smoke_shard": {"index": shard[0], "count": shard[1]} if args.suite in SMOKE_SUITES else None}
    write_json(artifacts / "summary.json", report)
    print("Evidence: " + str(artifacts), flush=True)
    control = ExecutionControl()
    with controlled_signals(control):
        try:
            if sys.platform != "darwin":
                raise RunnerError("Actual iOS tests require macOS and full Xcode.")
            with runner_lock(cache / "runner.lock") as lock_fd:
                run = Run(root, artifacts, cache, lock_fd, control=control, smoke_tests=smoke_tests)
                with verified_sources(run, report):
                    execute_suite(run, args.suite, report)
                control.check()
            report["status"] = "passed"
        except (Exception, KeyboardInterrupt) as error:
            report["status"] = "failed"
            report["error"] = str(error) or "Interrupted; inspect retained command logs."
            print(report["error"], file=sys.stderr)
        finally:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_json(artifacts / "summary.json", report)
    print("Result: " + report["status"] + "; evidence: " + str(artifacts), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
