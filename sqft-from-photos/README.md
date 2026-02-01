# sqft-from-photos (RunPod-first)

Photo-only CV pipeline that estimates apartment **square footage** (sqft) from listing photos using:

- **SfM** (COLMAP, with optional learned matching via LightGlue)
- **Metric depth** (DepthAnythingV2 metric by default; optional Metric3D/UniDepth/MoGe/ZoeDepth)
- **Scale alignment** (align SfM depths to metric depth maps)
- **Footprint extraction** (fit floor plane → 2D footprint → sqft)
- **Uncertainty** (heuristic or Monte Carlo) + **post-hoc calibration** (conformal)

This folder is designed so you can:

1) spin up a RunPod GPU pod,
2) `git clone` (or sparse checkout) just `sqft-from-photos/`,
3) run one bootstrap script,
4) upload a few listing photo folders,
5) run the pipeline end-to-end and get results under `/workspace` (persistent volume).

## Fast start (RunPod SSH runbook)

RunPod pod settings (recommended for a 1× RTX 4090, 24GB):

- Container image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- Volume mount path: `/workspace` (persistent)
- Volume disk: **>= 100GB** (safe for “full plan” experiments)
- Container disk: **>= 50GB** (COLMAP DBs + intermediates; ephemeral)
- Expose: TCP `22` (SSH), HTTP `8888` (optional for notebooks)

After you SSH into the pod:

```bash
cd /workspace

# Clone the repo (full clone)
git clone https://github.com/umqadir/streeteasy-enhanced-extension.git
cd streeteasy-enhanced-extension/sqft-from-photos

# Or: sparse checkout ONLY sqft-from-photos (faster)
# git clone --filter=blob:none --sparse https://github.com/umqadir/streeteasy-enhanced-extension.git
# cd streeteasy-enhanced-extension
# git sparse-checkout set sqft-from-photos
# cd sqft-from-photos

# One-time per pod (re-runnable):
bash cv-pipeline/scripts/runpod_bootstrap.sh
source /workspace/cv_pipeline_env.sh

# Sanity check the environment:
cd cv-pipeline
uv run python scripts/doctor.py

# Download the default model used by eval runs:
uv run python scripts/download_models.py depth-anything-metric --encoder vitl --dataset hypersim
```

Single-command variant (does bootstrap + doctor + default model download):

```bash
bash cv-pipeline/scripts/runpod_quickstart.sh
source /workspace/cv_pipeline_env.sh
```

### Upload your photos (3–5 listings)

For the quickest “does it work end-to-end?” test, upload 3–5 listing folders like:

```
/workspace/data/listing_a/  (jpg/png/webp inside; subfolders OK)
/workspace/data/listing_b/
...
```

Then run:

```bash
uv run cv-pipeline run \
  --images /workspace/data/listing_a \
  --colmap \
  --sfm-matching exhaustive
```

Outputs are written to `"$CVP_VOLUME/runs/<run_id>/"` (defaults to `/workspace/runs/...`).

## Curate photos (no GUI)

Most listings include exterior shots / amenity photos. The pipeline supports **include/exclude filters** so you can test quickly without re-uploading.

### Option A (simplest): only upload interior photos

On your local machine, copy interior photos into a clean folder and upload just that folder to the pod.

### Option B: filters by index / glob / filename

1) Print stable indices for a listing folder:

```bash
uv run cv-pipeline list-images --images /workspace/data/listing_a
```

2) Create a filter file (example `/workspace/filters/listing_a.txt`):

```
# keep only the first 12
include_index: 0-11

# drop a couple
exclude_index: 7,8
```

3) Run with filters:

```bash
uv run cv-pipeline run \
  --images /workspace/data/listing_a \
  --filter-file /workspace/filters/listing_a.txt \
  --colmap --sfm-matching exhaustive
```

`run_id` outputs include `selected_images.txt` so you can see exactly what was used.

## Local photo curation GUI (recommended)

To avoid doing any selection/exclusion on RunPod, curate a “clean set” locally and upload it as-is.

Run the local web UI against the evaluation dataset:

```bash
python sample-collection/scripts/curate_web.py --dataset sample-collection/streeteasy_eval_dataset/listings.json
```

Open the printed URL, exclude bad photos, optionally type the listing sqft, and click “Export listing”.

This creates an export folder like:

```
<repo>/sqft-from-photos/clean_set_export/
  listings.json
  photos/<listing_id>/photo_00.jpg ...
```

### RunPod “drag & drop” location (VS Code)

Copy the entire `clean_set_export/` folder to:

```
/workspace/data/streeteasy_clean_set/
```

Then run:

```bash
cd /workspace/streeteasy-enhanced-extension/sqft-from-photos/cv-pipeline
uv run cv-pipeline eval-streeteasy --dataset /workspace/data/streeteasy_clean_set/listings.json --has-sqft
```

## Run Streeteasy eval dataset (optional)

This repo includes metadata at `sample-collection/streeteasy_eval_dataset/listings.json`, but the photos are **gitignored** and must be uploaded to your pod at the matching path:

```
.../streeteasy_eval_dataset/
  listings.json
  photos/listing_001/photo_00.jpg
  ...
```

List which IDs have sqft labels:

```bash
uv run cv-pipeline list-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --has-sqft
```

Run eval on just the labeled listings (best for initial verification):

```bash
uv run cv-pipeline eval-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --has-sqft \
  --limit 5
```

### Generate per-listing filter templates + contact sheets

If you want a quick “edit text files, rerun eval” loop, generate templates:

```bash
uv run cv-pipeline curate-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --has-sqft \
  --limit 5
```

This writes:

- `"$CVP_VOLUME/curation/<id>/filters/<listing_id>.txt"` (edit these)
- `"$CVP_VOLUME/curation/<id>/listings/<listing_id>/manifest.json"` (index → filename)
- `"$CVP_VOLUME/curation/<id>/listings/<listing_id>/contact_sheet.jpg"` (copy to your laptop to view)

Then run eval with those filters:

```bash
uv run cv-pipeline eval-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --has-sqft \
  --filters-dir "$CVP_VOLUME/curation/<id>/filters" \
  --limit 5
```

## Experiments / sweeps

Built-in sweep (small but useful):

```bash
uv run cv-pipeline sweep-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --has-sqft \
  --limit 5
```

Custom sweep config (JSON):

```json
{
  "base": {
    "sfm_matching": "lightglue",
    "fusion": "tsdf",
    "uncertainty": "montecarlo"
  },
  "grid": {
    "depth_model": ["depth-anything-metric", "metric3d-v2"],
    "multi_component": ["best", "sum"]
  }
}
```

Run it:

```bash
uv run cv-pipeline sweep-streeteasy \
  --dataset ../sample-collection/streeteasy_eval_dataset/listings.json \
  --has-sqft \
  --limit 5 \
  --config /workspace/sweep.json
```

## Storage + VRAM sizing (verify on your pod)

Storage estimate without downloading:

```bash
python cv-pipeline/scripts/estimate_storage.py
```

VRAM profiling (after downloading the “full plan” model set):

```bash
cd cv-pipeline
uv run python scripts/download_models.py all
uv run python scripts/profile_vram.py all --amp --n-images 4 --size 512 --print-json \
  --json-out "$CVP_VOLUME/runs/vram_profile.json"
```

## Keys / Codex / Claude Code (don’t leak secrets)

- Put `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` into **RunPod Secrets** (preferred) or env vars.
- Don’t commit keys into git.
- Don’t write keys into files under `/workspace` unless you accept they persist on the volume.

`cv-pipeline/scripts/runpod_bootstrap.sh` installs the CLIs but does not store keys.

## Repository map

- `cv-pipeline/src/cv_pipeline/cli.py`: CLI entrypoint (`cv-pipeline ...`)
- `cv-pipeline/src/cv_pipeline/pipeline/runner.py`: core orchestration
- `cv-pipeline/scripts/runpod_bootstrap.sh`: RunPod bootstrap
- `cv-pipeline/docs/PROJECT-PLAN.md`: long-form research roadmap (non-training scope is implemented)

## Data collection (optional, later)

If/when you want to collect more StreetEasy examples, scripts live under `sample-collection/`.
