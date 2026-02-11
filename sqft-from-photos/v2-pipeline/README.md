# sqft-from-photos v2

Estimate visible floor area from room photos. Each pipeline variant is a standalone script.

## Variants

| Script | Approach | Status |
|--------|----------|--------|
| `estimate_v2a.py` | DUSt3R + Depth-Anything-V2 (no segmentation) | Baseline, overestimates |
| `estimate_v2b.py` | SegFormer + MoGe-2 (segmentation + metric depth) | Current best |

See `experiments.md` for detailed results and model inventory.

## Quick Start

```bash
cd sqft-from-photos/v2-pipeline
uv sync

# Latest variant, interactive (pick same-room photos)
uv run python estimate_v2b.py

# Batch mode
uv run python estimate_v2b.py /path/to/room/photos/

# With JSON output
uv run python estimate_v2b.py photos/ --json result.json
```

## Test With Your Own Photos

1. Take 3-4 photos of the **same room** from different corners/angles
2. AirDrop / copy them to a folder
3. Run `uv run python estimate_v2b.py` and pick the photos interactively

Tips:
- Shoot from corners looking inward, with floor visible
- 3-4 angles of one room is ideal
- Avoid close-ups of single objects or photos of different rooms

## How v2b Works

1. **SegFormer-B5** (ADE20K) segments floor+rug pixels
2. **MoGe-2** predicts metric 3D points, surface normals, and camera intrinsics
3. Floor points = segmentation mask AND upward-facing normals
4. RANSAC fits a plane to floor points; planarity residual = quality metric
5. Alpha-shape on projected points gives visible floor area
6. Multi-image: fuse per-image areas (max coverage)

## Requirements

Model weights cached at `~/.cache/cv_pipeline/models/` (MoGe-2 vendor + checkpoint).
SegFormer-B5 downloads from HuggingFace on first run (~350 MB).
