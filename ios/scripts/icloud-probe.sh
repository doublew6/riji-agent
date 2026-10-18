#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_dir="${TMPDIR:-/tmp}/riji-ios-icloud-probe-cli"
mkdir -p "$build_dir"
swiftc "$script_dir/../ProbeSupport/ProbeFixture.swift" \
  "$script_dir/../ProbeSupport/ProbeOperations.swift" \
  "$script_dir/icloud-probe/main.swift" -o "$build_dir/icloud-probe"
exec "$build_dir/icloud-probe" "$@"
