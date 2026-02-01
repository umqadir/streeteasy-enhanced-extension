# sqft-from-photos

CV pipeline to estimate apartment square footage from listing photos.

**Pipeline**: SfM (COLMAP) → Metric Depth → Scale Alignment → Floor Extraction → sqft

## Quick Start (RunPod)

```bash
# First time only
cd /workspace
git clone https://github.com/umqadir/streeteasy-enhanced-extension.git
cd streeteasy-enhanced-extension/sqft-from-photos
bash cv-pipeline/scripts/runpod_bootstrap.sh
source /workspace/cv_pipeline_env.sh
cd cv-pipeline
bash scripts/build_colmap_cuda.sh              # ~20 min, once
uv run python scripts/download_models.py all   # ~13 GB, once

# After pod restart
source /workspace/cv_pipeline_env.sh
cd /workspace/streeteasy-enhanced-extension/sqft-from-photos/cv-pipeline

# Run on a listing
uv run cv-pipeline run \
  --images /workspace/data/streeteasy_clean_set/photos/listing_073 \
  --colmap --sfm-matching exhaustive
```

## CLI Commands

```bash
# Single listing
uv run cv-pipeline run --images <path> --colmap

# Eval on dataset
uv run cv-pipeline eval-streeteasy --dataset <listings.json> --limit 10

# List available images
uv run cv-pipeline list-images --images <path>

# Check environment
uv run python scripts/doctor.py
```

## Key Options

| Option | Default | Notes |
|--------|---------|-------|
| `--colmap` | off | Enable SfM reconstruction |
| `--sfm-matching` | exhaustive | Or `lightglue` for learned matching |
| `--depth-model` | depth-anything-metric | Or `metric3d-v2`, `unidepth-v1`, `ensemble` |
| `--fusion` | none | Or `tsdf` for dense fusion |
| `--fallback` | depth-only | Or `dust3r`, `mast3r` |

## Docs

- [cv-pipeline/docs/RUNPOD.md](cv-pipeline/docs/RUNPOD.md) - Pod workflow & troubleshooting
- [cv-pipeline/docs/PROJECT-PLAN.md](cv-pipeline/docs/PROJECT-PLAN.md) - Technical design & research roadmap

## Project Structure

```
sqft-from-photos/
├── cv-pipeline/          # Main pipeline code
│   ├── src/cv_pipeline/  # Python package
│   ├── scripts/          # Setup & utility scripts
│   └── docs/             # Documentation
├── sample-collection/    # Data collection tools (local)
└── README.md
```

## RunPod Settings

- **Image**: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- **Volume**: 70-100 GB at `/workspace`
- **GPU**: RTX 4090 (24 GB) handles everything
