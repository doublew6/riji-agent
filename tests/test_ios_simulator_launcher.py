"""Check launcher failures without touching Xcode, Simulator, or user records."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_ios_simulator.sh"
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac-only launcher")


def run_launcher(tmp_path, *, devices=None, runtime=True, xcode=True, args=(), device_types=None):
    """Supply a temporary tool environment and record every attempted mutation."""
    tools = tmp_path / "bin"
    tools.mkdir()
    developer = tmp_path / "Xcode"
    if xcode:
        (developer / "Applications/Simulator.app").mkdir(parents=True)
    fixture = tmp_path / "fixtures"
    fixture.mkdir()
    (fixture / "devices.json").write_text(json.dumps({"devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-26-5": devices or []}}))
    (fixture / "runtimes.json").write_text(json.dumps({"runtimes": [{
        "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
        "version": "26.5", "isAvailable": runtime}]}))
    (fixture / "types.json").write_text(json.dumps({"devicetypes": [{
        "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
        "name": "iPhone 17 Pro", "minRuntimeVersion": 26 << 16,
        "maxRuntimeVersion": (26 << 16) | 65535}]}))
    if device_types is not None:
        (fixture / "types.json").write_text(json.dumps({"devicetypes": device_types}))
    scripts = {
        "xcode-select": 'printf "%s\\n" "$LAUNCHER_TEST_DEVELOPER"',
        "xcodebuild": 'if [ "$1" = -version ]; then exit 0; fi\n'
        'echo build >> "$LAUNCHER_TEST_CALLS"\nexit 9',
        "open": 'echo open >> "$LAUNCHER_TEST_CALLS"',
        "xcrun": '''if [ "$1" = --find ]; then exit 0; fi
if [ "$2" = list ]; then
    if [ "$3" = devices ] && [ "$4" != -j ]; then
        echo "Device discovery must include unavailable devices" >&2
        exit 9
    fi
    case "$3" in devicetypes) name=types ;; *) name="$3" ;; esac
    cat "$LAUNCHER_TEST_FIXTURES/$name.json"
    exit 0
fi
echo "$*" >> "$LAUNCHER_TEST_CALLS"
case "$2" in create) echo synthetic-device ;; esac
''',
    }
    for name, body in scripts.items():
        target = tools / name
        target.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        target.chmod(0o700)
    calls = tmp_path / "calls"
    environment = dict(os.environ, PATH=f"{tools}:/usr/bin:/bin", TMPDIR=str(tmp_path),
                       LAUNCHER_TEST_DEVELOPER=str(developer),
                       LAUNCHER_TEST_CALLS=str(calls), LAUNCHER_TEST_FIXTURES=str(fixture),
                       LAUNCHER_TEST_PYTHON=sys.executable)
    # The system Python shim can require Xcode setup. These tests exercise only
    # synthetic discovery and must use their already-running Python interpreter.
    source = SCRIPT.read_text()
    assert source.count('/usr/bin/python3 - ') == 1
    source = source.replace('/usr/bin/python3 - ', '"$LAUNCHER_TEST_PYTHON" - ')
    result = subprocess.run(["/bin/bash", "-c", source, str(SCRIPT), *args], env=environment,
                            capture_output=True, text=True, timeout=20)
    return result, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize("args", [("--invalid",), ("--sample", "extra")])
def test_bad_arguments_do_not_touch_simulator(tmp_path, args):
    result, calls = run_launcher(tmp_path, args=args)
    assert result.returncode == 2
    assert calls == []


def test_missing_xcode_exits_before_simulator_changes(tmp_path):
    result, calls = run_launcher(tmp_path, xcode=False)
    assert result.returncode == 1
    assert "Full Xcode is required" in result.stderr
    assert calls == []


def test_missing_runtime_exits_before_device_creation(tmp_path):
    result, calls = run_launcher(tmp_path, runtime=False)
    assert result.returncode != 0
    assert "No available iOS Simulator runtime" in result.stderr
    assert calls == []


def test_duplicate_devices_are_left_untouched(tmp_path):
    device = {"name": "Riji Development iPhone", "state": "Booted", "udid": "test-1", "isAvailable": True}
    result, calls = run_launcher(tmp_path, devices=[device, dict(device, udid="test-2")])
    assert result.returncode != 0
    assert "Multiple Riji Development iPhone devices" in result.stderr
    assert calls == []


def test_build_failure_does_not_replace_existing_app_or_data(tmp_path):
    device = {"name": "Riji Development iPhone", "state": "Booted", "udid": "test-1", "isAvailable": True}
    result, calls = run_launcher(tmp_path, devices=[device], args=("--sample",))
    assert result.returncode == 1
    assert "existing app and its data were not replaced" in result.stderr
    assert calls == ["open", "simctl bootstatus test-1 -b", "build"]


def test_unavailable_existing_device_is_not_replaced(tmp_path):
    device = {"name": "Riji Development iPhone", "state": "Shutdown",
              "udid": "preserved-old-device", "isAvailable": False}
    result, calls = run_launcher(tmp_path, devices=[device])
    assert result.returncode != 0
    assert "Existing Riji Development iPhone is unavailable" in result.stderr
    assert calls == []


def test_creation_uses_type_with_matching_minimum_and_maximum_runtime(tmp_path):
    modern = {"name": "iPhone 17 Pro", "identifier": "synthetic-compatible-iphone",
              "minRuntimeVersion": 26 << 16, "maxRuntimeVersion": (26 << 16) | 65535}
    future = {"name": "iPhone Future", "identifier": "synthetic-future-iphone",
              "minRuntimeVersion": 27 << 16, "maxRuntimeVersion": 0xffffffff}
    legacy = {"name": "iPhone 6s Plus", "identifier": "synthetic-legacy-iphone",
              "minRuntimeVersion": 9 << 16, "maxRuntimeVersion": (15 << 16) | 65535}
    result, calls = run_launcher(tmp_path, device_types=[modern, future, legacy])
    assert result.returncode == 1  # The stub deliberately fails the later build.
    assert calls[0] == ("simctl create Riji Development iPhone synthetic-compatible-iphone "
                        "com.apple.CoreSimulator.SimRuntime.iOS-26-5")
    assert not any("synthetic-future-iphone" in call or "synthetic-legacy-iphone" in call for call in calls)
    assert not any("erase" in call or "uninstall" in call for call in calls)


def test_no_compatible_type_stops_before_creating_device(tmp_path):
    legacy = {"name": "iPhone 6s Plus", "identifier": "synthetic-legacy-iphone",
              "minRuntimeVersion": 9 << 16, "maxRuntimeVersion": (15 << 16) | 65535}
    result, calls = run_launcher(tmp_path, device_types=[legacy])
    assert result.returncode != 0
    assert "No compatible iPhone Simulator device type" in result.stderr
    assert calls == []
