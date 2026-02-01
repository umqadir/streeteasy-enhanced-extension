#!/usr/bin/env bash
set -euo pipefail

export CVP_VOLUME="${CVP_VOLUME:-/runpod-volume}"
export CVP_WORKDIR="${CVP_WORKDIR:-/tmp/cv_pipeline_work}"

echo "CVP_VOLUME=$CVP_VOLUME"
echo "CVP_WORKDIR=$CVP_WORKDIR"

echo
echo "1) System deps (COLMAP)"
echo "Run (as root): bash cv-pipeline/scripts/runpod_setup_system.sh"

echo
echo "2) Python deps"
echo "cd cv-pipeline && uv sync --extra gpu"

echo
echo "3) Download default depth model"
echo "uv run python cv-pipeline/scripts/download_models.py depth-anything-metric --encoder vitl --dataset hypersim"

