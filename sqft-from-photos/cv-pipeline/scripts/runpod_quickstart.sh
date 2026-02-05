#!/usr/bin/env bash
set -euo pipefail

# Backwards-compatible alias for older docs/commands.
# Prefer: bash cv-pipeline/scripts/runpod_bootstrap.sh

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$script_dir/runpod_bootstrap.sh" "$@"

