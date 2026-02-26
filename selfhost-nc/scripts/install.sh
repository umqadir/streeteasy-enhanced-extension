#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SKIP_MODELS=0
for arg in "$@"; do
  case "$arg" in
    --skip-models) SKIP_MODELS=1 ;;
    *) echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install from https://docs.astral.sh/uv/"
  exit 1
fi

echo "[1/3] Syncing backend Python env (Python 3.11 target)..."
uv sync --project backend --python 3.11

if [[ "$SKIP_MODELS" -eq 0 ]]; then
  echo "[2/3] Downloading model assets (DUSt3R + MoGe)..."
  uv run --project backend python scripts/bootstrap_models.py
else
  echo "[2/3] Skipping model download (--skip-models)"
fi

echo "[3/3] Running doctor checks..."
bash scripts/doctor.sh

echo
echo "Install complete."
echo "Start backend: bash scripts/start_backend.sh"
echo "Load extension: chrome://extensions -> Load unpacked -> $ROOT_DIR/extension"
