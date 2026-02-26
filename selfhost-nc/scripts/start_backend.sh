#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8787}"

for arg in "$@"; do
  if [[ "$arg" == "--mode" || "$arg" == --mode=* ]]; then
    echo "This release is local-backend only. --mode is not supported."
    exit 2
  fi
done

uv run --project backend python backend/local_backend.py \
  --host "$HOST" \
  --port "$PORT" \
  --device-policy auto \
  --analysis-mode auto \
  "$@"
