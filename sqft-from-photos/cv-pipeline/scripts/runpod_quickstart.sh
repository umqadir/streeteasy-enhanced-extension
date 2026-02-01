#!/usr/bin/env bash
set -euo pipefail

# One-shot setup for a fresh RunPod pod.
#
# - Installs system deps (COLMAP), Python env (uv), and Codex/Claude CLIs.
# - Writes /workspace/cv_pipeline_env.sh (or under CVP_VOLUME).
# - Runs a small doctor check.
# - Downloads the default depth model used by eval runs.
#
# Safe to re-run (downloads are skipped if already present).

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/.." && pwd)"

bash "$project_dir/scripts/runpod_bootstrap.sh"

if [[ -f "${CVP_VOLUME:-/workspace}/cv_pipeline_env.sh" ]]; then
  # shellcheck disable=SC1090
  source "${CVP_VOLUME:-/workspace}/cv_pipeline_env.sh"
elif [[ -f "/workspace/cv_pipeline_env.sh" ]]; then
  # shellcheck disable=SC1091
  source "/workspace/cv_pipeline_env.sh"
fi

cd "$project_dir"

uv run python scripts/doctor.py
uv run python scripts/download_models.py depth-anything-metric --encoder vitl --dataset hypersim

cat <<EOF

Quickstart complete.

Next:
  - Run on a listing folder:
      uv run cv-pipeline run --images /workspace/data/listing_a --colmap --sfm-matching exhaustive
  - Or run eval (if you uploaded streeteasy_eval_dataset/photos):
      uv run cv-pipeline eval-streeteasy --dataset ../sample-collection/streeteasy_eval_dataset/listings.json --has-sqft --limit 5
EOF

