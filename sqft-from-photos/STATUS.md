# sqft-from-photos — current status

This folder contains:

- `cv-pipeline/`: a runnable, photo-only CV pipeline (COLMAP + metric depth) that outputs sqft + diagnostics
- `sample-collection/`: a small labeled dataset + downloaded example photos for quick evals

The long-form research roadmap is in `cv-pipeline/docs/PROJECT-PLAN.md`.

## What’s implemented (today)

- End-to-end pipeline that can run on a set of listing photos:
  - Image preprocessing (resize + copy)
  - SfM with COLMAP (feature_extractor → exhaustive_matcher → mapper)
  - Metric depth via Depth-Anything-V2 metric (Hypersim + ViT-L default)
  - Robust scale fit between SfM depths and metric depths
  - Point cloud build + floor plane fit + footprint extraction
  - Sqft estimate + a basic uncertainty interval + diagnostics JSON
- “Download once, reuse” caching onto `CVP_VOLUME`:
  - `TORCH_HOME`, `HF_HOME`, `TRANSFORMERS_CACHE`, `HUGGINGFACE_HUB_CACHE` are redirected to `"$CVP_VOLUME/models/..."`
- RunPod tooling:
  - `cv-pipeline/scripts/runpod_setup_system.sh` installs COLMAP and common libs
  - `cv-pipeline/scripts/runpod_bootstrap.sh` sets up the pod (system + python + Codex/Claude CLIs)
  - `cv-pipeline/scripts/estimate_storage.py` estimates weight footprint without downloading
  - `cv-pipeline/scripts/profile_vram.py` profiles peak CUDA VRAM for heavy research-plan models (after download)

## What’s not implemented yet (planned in PROJECT-PLAN)

- Retrieval + learned matching (hloc / LightGlue) feeding COLMAP instead of exhaustive matching
- DUSt3R/MASt3R as an actual reconstruction backend (currently: downloadable + VRAM-profileable only)
- TSDF fusion / meshing (e.g., Open3D integration) and more robust floor/room segmentation
- Better uncertainty calibration (current interval is heuristic; needs calibration on labeled data)
- Stronger “single-view fallback” beyond depth-only point cloud

## RunPod: fastest “clone → run” workflow

Recommended settings for a 1× RTX 4090 (24GB):

- **Volume disk (persistent, mounted at `/workspace`)**: `>=100GB` (20GB is too small for the full plan)
- **Container disk (ephemeral)**: `>=50GB`

Why `>=100GB` volume?

- The full research-plan model weights are ~`13GB` by `python cv-pipeline/scripts/estimate_storage.py`
- Real usage adds HF cache overhead + vendor repos + outputs; 100GB avoids constantly juggling space

### 1) SSH in and bootstrap

```bash
cd /workspace
git clone https://github.com/umqadir/streeteasy-enhanced-extension.git
cd streeteasy-enhanced-extension/sqft-from-photos

bash cv-pipeline/scripts/runpod_bootstrap.sh
source /workspace/cv_pipeline_env.sh
```

### 2) Download the default depth model (used by the pipeline)

```bash
cd cv-pipeline
uv run python scripts/download_models.py depth-anything-metric --encoder vitl --dataset hypersim
```

### 3) Run on the included sample photos

```bash
uv run cv-pipeline eval-streeteasy \
  --dataset ../sample-collection/data/streeteasy_examples_20.json \
  --downloads ../sample-collection/data/downloads \
  --limit 3
```

Outputs go to `"$CVP_VOLUME/runs/<run_id>/"` (so they persist on the volume).

## Verify VRAM + storage before you scale down

Storage (no downloads):

```bash
python cv-pipeline/scripts/estimate_storage.py
```

VRAM (after downloading the full plan set):

```bash
cd cv-pipeline
uv run python scripts/download_models.py all
uv run python scripts/profile_vram.py all --amp --n-images 4 --size 512 --print-json \
  --json-out "$CVP_VOLUME/runs/vram_profile.json"
```

## Codex + Claude Code without leaking keys

- Put `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` into **RunPod “Secrets”** (preferred) or env vars.
- Do not commit keys into git, and avoid writing them to shell rc files on the volume.
- `cv-pipeline/scripts/runpod_bootstrap.sh` installs the CLIs but does not prompt for or store keys.

## “Continue from here” prompt for an agent

Paste this into Codex/Claude Code after you `cd` into `.../sqft-from-photos` on the pod:

> Goal: get `cv-pipeline eval-streeteasy` running end-to-end on 3 sample listings, saving outputs under `$CVP_VOLUME/runs/`.
> Constraints: no GUI; use `uv`; don’t download models to container disk (keep them on `/workspace`).
> Steps: run `bash cv-pipeline/scripts/runpod_bootstrap.sh`, download the default depth model, run eval, and fix any missing system deps / path issues.
