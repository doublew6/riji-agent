#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! ./scripts/run_ios_simulator.sh "$@"; then
    echo 'Riji could not start. Read the error above; no Simulator data was erased.'
    read -r -p 'Press Return to close…' _answer
    exit 1
fi
