# sqft-from-photos — current status

This folder contains:

- `cv-pipeline/`: a runnable, photo-only CV pipeline (COLMAP + metric depth) that outputs sqft + diagnostics
- `sample-collection/`: a small labeled dataset + downloaded example photos for quick evals

The long-form research roadmap is in `cv-pipeline/docs/PROJECT-PLAN.md`.

## What’s implemented (today)

- End-to-end geometry pipeline (multi-view first):
  - Image preprocessing (resize + dedup)
  - Overlap graph via embeddings (for selective matching + clustering)
  - SfM with COLMAP:
    - `--sfm-matching exhaustive` (vanilla COLMAP)
    - `--sfm-matching lightglue` (retrieval → LightGlue matches → COLMAP mapper)
  - Metric depth backends:
    - DepthAnythingV2 metric, Metric3D v2, UniDepth v1, MoGe v2, ZoeDepth
    - `--depth-model ensemble` to mix multiple depth models
  - Metric scale recovery by aligning SfM depths to metric depth maps
  - Dense fusion option: `--fusion tsdf` (Open3D TSDF)
  - Floor plane fit + footprint extraction
  - Multi-component handling when COLMAP fragments:
    - `--multi-component best` (use best sub-model)
    - `--multi-component sum` (conservative aggregation w/ overlap clustering)
  - Uncertainty:
    - `--uncertainty heuristic`
    - `--uncertainty montecarlo` (saves sample arrays to `runs/<run_id>/samples_*.npy`)
  - Post-hoc calibration:
    - `cv-pipeline calibrate` (conformal interval expansion)
    - `cv-pipeline report-eval` (coverage/width metrics, optional calibration)
- “Download once, reuse” caching onto `CVP_VOLUME`:
  - `TORCH_HOME`, `HF_HOME`, `TRANSFORMERS_CACHE`, `HUGGINGFACE_HUB_CACHE` are redirected to `"$CVP_VOLUME/models/..."`
  - `XDG_CACHE_HOME`, `MPLCONFIGDIR` redirected to `"$CVP_VOLUME/.cache/..."`
- RunPod tooling:
  - `cv-pipeline/scripts/runpod_setup_system.sh` installs COLMAP and common libs
  - `cv-pipeline/scripts/runpod_bootstrap.sh` sets up the pod (system + python + Codex/Claude CLIs)
  - `cv-pipeline/scripts/estimate_storage.py` estimates weight footprint without downloading
  - `cv-pipeline/scripts/profile_vram.py` profiles peak CUDA VRAM for heavy research-plan models (after download)
- Fallbacks (when COLMAP fails):
  - `--fallback depth-only` (depth-only + heuristic layout prior + clustering)
  - `--fallback dust3r` / `--fallback mast3r` (DUSt3R/MASt3R recon + metric scale via depth alignment)

## What’s not implemented yet (planned in PROJECT-PLAN)

- Domain-specific training / fine-tuning (explicitly deferred)
- Object-anchor scale constraints (doors/counters/beds) as an additional scale likelihood
- Multi-floor unit handling beyond “dominant floor plane”

## RunPod: fastest “clone → run” workflow

Recommended settings for a 1× RTX 4090 (24GB):

- **Volume disk (persistent, mounted at `/workspace`)**: `>=100GB` (20GB is too small for the full plan)
- **Container disk (ephemeral)**: `>=50GB` (COLMAP DBs + intermediates can be large)

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

If you plan to use LightGlue / UniDepth / MoGe / Metric3D / DUSt3R / MASt3R:

```bash
uv run python scripts/download_models.py all
```

### 3) Run on the included sample photos

```bash
uv run cv-pipeline eval-streeteasy \
  --dataset ../sample-collection/data/streeteasy_examples_20.json \
  --downloads ../sample-collection/data/downloads \
  --limit 3
```

If you uploaded the larger evaluation bundle (`sample-collection/streeteasy_eval_dataset/`), run:

```bash
uv run cv-pipeline eval-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --limit 10
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

> Goal: run `cv-pipeline eval-streeteasy` end-to-end on 3 sample listings and then run a small sweep.
> Constraints: no GUI; use `uv`; keep caches/models on `/workspace` (network volume); don’t commit secrets.
> Steps:
> 1) `bash cv-pipeline/scripts/runpod_bootstrap.sh && source /workspace/cv_pipeline_env.sh`
> 2) `cd cv-pipeline && uv run python scripts/download_models.py all`
> 3) `uv run cv-pipeline eval-streeteasy --dataset ../sample-collection/streeteasy_eval_dataset/listings.json --limit 10`
> 4) `uv run cv-pipeline sweep-streeteasy --dataset ../sample-collection/streeteasy_eval_dataset/listings.json --limit 10`
