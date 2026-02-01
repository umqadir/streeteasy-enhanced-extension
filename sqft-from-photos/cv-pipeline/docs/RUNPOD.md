# RunPod Workflow

Simple workflow for a solo dev. Assumes `/workspace` persists across pod restarts.

## After Pod Restart (10 seconds)

```bash
source /workspace/cv_pipeline_env.sh
cd /workspace/streeteasy-enhanced-extension/sqft-from-photos/cv-pipeline
```

That's it. Everything is already there.

## First-Time Setup (One Time)

```bash
cd /workspace
git clone https://github.com/umqadir/streeteasy-enhanced-extension.git
cd streeteasy-enhanced-extension/sqft-from-photos

# Install deps + create env file
bash cv-pipeline/scripts/runpod_bootstrap.sh
source /workspace/cv_pipeline_env.sh

# Build COLMAP with CUDA (~20 min, persists forever)
cd cv-pipeline
bash scripts/build_colmap_cuda.sh

# Download all models (~13 GB, persists forever)
uv run python scripts/download_models.py all

# Verify
uv run python scripts/doctor.py
```

## Running Experiments

```bash
cd /workspace/streeteasy-enhanced-extension/sqft-from-photos/cv-pipeline

# Single listing
uv run cv-pipeline run \
  --images /workspace/data/streeteasy_clean_set/photos/listing_073 \
  --colmap \
  --sfm-matching exhaustive

# Batch eval
uv run cv-pipeline eval-streeteasy \
  --dataset /workspace/data/streeteasy_clean_set/listings.json \
  --limit 10
```

## What's On Your Volume

```
/workspace/
├── cv_pipeline_env.sh           # Source this after restart
├── models/                      # ~15 GB (checkpoints + vendor repos)
├── tools/colmap/                # ~500 MB (CUDA COLMAP binary)
├── .cache/                      # ~12 GB (uv/pip cache)
├── streeteasy-enhanced-extension/  # Code + venv
├── data/                        # Your photos
├── work/                        # Pipeline intermediates
└── runs/                        # Pipeline outputs
```

## Resource Requirements

| GPU | Works For |
|-----|-----------|
| RTX 4090 (24 GB) | Everything |
| RTX 3090 (24 GB) | Everything |
| 16 GB GPU | Standard pipeline, no DUSt3R/MASt3R |

Volume: 70-100 GB recommended.

## Git Authentication (for pushing changes)

1. Create a GitHub Personal Access Token (PAT):
   - GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
   - Create with `repo` scope

2. Add as RunPod Secret:
   - RunPod console → Settings → Secrets
   - Add `GITHUB_TOKEN` with your PAT

3. Enable the secret when creating/editing your pod

After sourcing the env file, git push/pull will work automatically.

## Common Issues

**COLMAP fails**: Rebuild with `bash scripts/build_colmap_cuda.sh`

**Out of VRAM**: Use `--depth-encoder vitb` or `--depth-input-size 384`

**cv-pipeline not found**: Run `uv sync` from the cv-pipeline directory
