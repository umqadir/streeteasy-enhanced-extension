#!/usr/bin/env bash
# Fast resume script for RunPod restarts.
# Run this after pod restart instead of full bootstrap.
#
# Usage: source cv-pipeline/scripts/runpod_resume.sh
#
# What it does:
#   1. Sources the persisted env file
#   2. Adds CUDA COLMAP to PATH (if built)
#   3. Quick uv sync (uses cached deps, very fast)
#   4. Runs doctor to verify everything works

set -euo pipefail

CVP_VOLUME="${CVP_VOLUME:-/workspace}"
ENV_FILE="$CVP_VOLUME/cv_pipeline_env.sh"
COLMAP_DIR="$CVP_VOLUME/tools/colmap"
PROJECT_DIR="$CVP_VOLUME/streeteasy-enhanced-extension/sqft-from-photos/cv-pipeline"

echo "=== RunPod Resume ==="
echo

# 1. Source env
if [[ -f "$ENV_FILE" ]]; then
  echo "Sourcing $ENV_FILE..."
  # shellcheck disable=SC1090
  source "$ENV_FILE"
else
  echo "WARNING: $ENV_FILE not found. Run full bootstrap first." >&2
fi

# 2. Add CUDA COLMAP to PATH if available
if [[ -x "$COLMAP_DIR/bin/colmap" ]]; then
  export PATH="$COLMAP_DIR/bin:$PATH"
  echo "Using CUDA COLMAP: $COLMAP_DIR/bin/colmap"
  colmap --help 2>&1 | head -2
else
  echo "NOTE: CUDA COLMAP not found at $COLMAP_DIR"
  echo "      Using system colmap (may not have CUDA)"
  echo "      To build: bash $PROJECT_DIR/scripts/build_colmap_cuda.sh"
fi
echo

# 3. Quick uv sync
if [[ -d "$PROJECT_DIR" ]]; then
  echo "Running uv sync (cached, should be fast)..."
  cd "$PROJECT_DIR"
  uv sync --extra gpu --extra sfm --extra depth --extra open3d --quiet
  echo "OK: Python environment ready"
else
  echo "WARNING: Project dir not found: $PROJECT_DIR" >&2
fi
echo

# 4. Quick doctor check
echo "Running quick health check..."
cd "$PROJECT_DIR"
uv run python scripts/doctor.py 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
checks = d.get('checks', [])
for c in checks:
    status = '✓' if c.get('ok') else '✗'
    print(f\"  {status} {c.get('name')}\")"

echo
echo "=== Ready! ==="
echo "Run experiments with:"
echo "  cd $PROJECT_DIR"
echo "  uv run cv-pipeline eval-streeteasy --dataset /workspace/data/streeteasy_clean_set/listings.json --limit 5"
