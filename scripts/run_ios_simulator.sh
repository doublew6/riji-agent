#!/bin/bash
# Build and open the dedicated development Simulator without erasing its data.
set -euo pipefail
mode="${1:-normal}"
case "$mode" in
    normal|--normal) mode=normal ;;
    --sample) mode=sample ;;
    --memory-overview) mode=memory-overview ;;
    --roundtable) mode=roundtable ;;
    *) echo "Usage: scripts/run_ios_simulator.sh [--normal|--sample|--memory-overview|--roundtable]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then echo "Unexpected arguments." >&2; exit 2; fi
project_root="$(cd "$(dirname "$0")/.." && pwd)"
simulator_name='Riji Development iPhone'
sample_namespace='743D25D2-4E3E-4B8B-A8DC-70D8A4A59B12'
memory_namespace='49D7B824-A7B7-4F53-A612-7A983264523E'
roundtable_namespace='6938B15E-4F63-4E93-B1E7-2C08587939D4'
bundle_id='com.riji.local'
derived_directory="${TMPDIR:-/tmp}/riji-ios-manual-derived"
if ! developer_directory="$(xcode-select -p 2>/dev/null)"; then
    echo "Full Xcode is required. Select the installed Xcode in Xcode Settings > Locations > Command Line Tools." >&2
    exit 1
fi
simulator_application="$developer_directory/Applications/Simulator.app"
device_hub_application="$(dirname "$developer_directory")/Applications/DeviceHub.app"
if [ -d "$device_hub_application" ]; then
    simulator_application="$device_hub_application"
elif [ ! -d "$simulator_application" ]; then
    echo "Full Xcode is required. Select the installed Xcode in Xcode Settings > Locations > Command Line Tools." >&2
    exit 1
fi
if ! xcrun --find simctl >/dev/null 2>&1 || ! xcodebuild -version >/dev/null 2>&1; then
    echo "Xcode tools are unavailable. Open Xcode and finish its first-launch setup." >&2
    exit 1
fi
launch_scratch="$(mktemp -d "${TMPDIR:-/tmp}/riji-launch.XXXXXX")"
trap 'rm -rf "$launch_scratch"' EXIT
xcrun simctl list devices -j > "$launch_scratch/devices.json"
xcrun simctl list runtimes -j > "$launch_scratch/runtimes.json"
xcrun simctl list devicetypes -j > "$launch_scratch/types.json"
/usr/bin/python3 - "$launch_scratch" "$simulator_name" <<'PY'
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
name = sys.argv[2]
devices = json.loads((root / 'devices.json').read_text())['devices']
runtimes = [r for r in json.loads((root / 'runtimes.json').read_text())['runtimes']
            if r.get('isAvailable') and '.iOS-' in r['identifier']]
existing = [(runtime, d) for runtime, group in devices.items() for d in group if d['name'] == name]
if len(existing) > 1:
    raise SystemExit('Multiple Riji Development iPhone devices exist. Resolve the duplicate names in Simulator before launching; no device was changed.')
if existing:
    runtime, device = existing[0]
    if not device.get('isAvailable') or runtime not in {r['identifier'] for r in runtimes}:
        raise SystemExit('Existing Riji Development iPhone is unavailable. Restore its iOS runtime in Xcode before launching; no device or data was changed.')
    (root / 'device').write_text(device['udid'])
    (root / 'state').write_text(device['state'])
else:
    if not runtimes:
        raise SystemExit('No available iOS Simulator runtime. Install one in Xcode Settings > Components, then rerun.')

    def runtime_version(value):
        parts = [int(n) for n in value.split('.')]
        parts += [0] * (3 - len(parts))
        return (parts[0] << 16) | (parts[1] << 8) | parts[2]

    runtimes.sort(key=lambda r: runtime_version(r['version']), reverse=True)
    types = [d for d in json.loads((root / 'types.json').read_text())['devicetypes']
             if d['name'].startswith('iPhone')
             and isinstance(d.get('minRuntimeVersion'), int)
             and isinstance(d.get('maxRuntimeVersion'), int)]
    selection = None
    for runtime in runtimes:
        version = runtime_version(runtime['version'])
        compatible = [d for d in types if d['minRuntimeVersion'] <= version <= d['maxRuntimeVersion']]
        if compatible:
            compatible.sort(key=lambda d: (d['minRuntimeVersion'], d['identifier']), reverse=True)
            selection = (runtime, compatible[0])
            break
    if selection is None:
        raise SystemExit('No compatible iPhone Simulator device type for an available iOS runtime. Install matching Xcode components, then rerun; no device was created.')
    (root / 'runtime').write_text(selection[0]['identifier'])
    (root / 'type').write_text(selection[1]['identifier'])
PY
if [ -f "$launch_scratch/device" ]; then
    simulator_id="$(cat "$launch_scratch/device")"
    simulator_state="$(cat "$launch_scratch/state")"
else
    simulator_id="$(xcrun simctl create "$simulator_name" "$(cat "$launch_scratch/type")" "$(cat "$launch_scratch/runtime")")"
    simulator_state=Shutdown
fi
case "$simulator_state" in
    Shutdown) xcrun simctl boot "$simulator_id" ;;
    Booted) ;;
    *) echo "Simulator is $simulator_state. Wait for it to finish, then rerun." >&2; exit 1 ;;
esac
if [ "$simulator_application" = "$device_hub_application" ]; then
    open -a "$simulator_application"
else
    open -a "$simulator_application" --args -CurrentDeviceUDID "$simulator_id"
fi
xcrun simctl bootstatus "$simulator_id" -b
mkdir -p "$derived_directory"
echo "Building Riji for $simulator_name. Existing app records are preserved."
# Xcode must embed simulator entitlements for the normal app's Keychain access.
if ! xcodebuild -project "$project_root/ios/Riji.xcodeproj" -scheme Riji -configuration Debug \
    -destination "platform=iOS Simulator,id=$simulator_id" -derivedDataPath "$derived_directory" \
    CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- build > "$derived_directory/manual-build.log" 2>&1; then
    tail -60 "$derived_directory/manual-build.log" >&2
    echo "Build failed. The existing app and its data were not replaced." >&2
    exit 1
fi
xcrun simctl install "$simulator_id" "$derived_directory/Build/Products/Debug-iphonesimulator/Riji.app"
# Restart only this app to select the requested namespace; never reset/uninstall/erase.
# Discard inherited fixture settings, including flags introduced by later tests.
while IFS= read -r variable; do
    case "$variable" in
        SIMCTL_CHILD_RIJI_TEST_*|SIMCTL_CHILD_RIJI_SAMPLE_MODE) unset "$variable" ;;
    esac
done < <(compgen -e)
export SIMCTL_CHILD_RIJI_TEST_CONTAINER='' SIMCTL_CHILD_RIJI_TEST_DATE='' SIMCTL_CHILD_RIJI_TEST_MOBILE=''
export SIMCTL_CHILD_RIJI_SAMPLE_MODE=''
case "$mode" in
    sample) export SIMCTL_CHILD_RIJI_TEST_CONTAINER="$sample_namespace" SIMCTL_CHILD_RIJI_SAMPLE_MODE=1 ;;
    memory-overview)
        export SIMCTL_CHILD_RIJI_TEST_CONTAINER="$memory_namespace" SIMCTL_CHILD_RIJI_SAMPLE_MODE=1
        export SIMCTL_CHILD_RIJI_TEST_MOBILE=overview ;;
    roundtable)
        export SIMCTL_CHILD_RIJI_TEST_CONTAINER="$roundtable_namespace" SIMCTL_CHILD_RIJI_SAMPLE_MODE=1
        export SIMCTL_CHILD_RIJI_TEST_MOBILE=roundtable ;;
esac
xcrun simctl launch --terminate-running-process "$simulator_id" "$bundle_id"
case "$mode" in
    normal) echo "Normal mode: local diary records are preserved; all inherited fixture flags are cleared." ;;
    sample) echo "SAMPLE mode: synthetic-only stable namespace UITests-$sample_namespace. Repeated sample launches reuse its records." ;;
    memory-overview) echo "MEMORY OVERVIEW demo: open the Memory tab. Synthetic service only; no Air connection or real memory changes." ;;
    roundtable) echo "ROUNDTABLE demo: open Mentors > Roundtable. Synthetic service only; no Air connection or real model calls." ;;
esac
echo "Riji is open. Feature demos and normal records use separate stable namespaces; no Simulator data was erased."
if [ "$mode" = sample ]; then
    echo "For sample import use Directory > Templates > Export synthetic sample to Files, then import that saved file."
fi
