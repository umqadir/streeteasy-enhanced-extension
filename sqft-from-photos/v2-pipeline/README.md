# sqft-from-photos v2

Current production path is `estimate_v2b.py`.

## Variants

| Script | Approach | Status |
|--------|----------|--------|
| `estimate_v2a.py` | Early baseline | Legacy |
| `estimate_v2b.py` | SegFormer + MoGe-2 + optional DUSt3R scene fusion | Current |

## Quick Start

```bash
cd sqft-from-photos/v2-pipeline
uv sync

# interactive picker
uv run python estimate_v2b.py

# batch (default: dust3r-scene visible-floor fusion)
uv run python estimate_v2b.py /path/to/room/photos --json result.json

# single-image-only path (no scene fusion)
uv run python estimate_v2b.py /path/to/room/photos --multiview-method single-image

# optional room completion (off by default)
uv run python estimate_v2b.py /path/to/room/photos --room-impute
```

## Inputs

Use 2-4 photos of the same room from slightly different angles.

- include clear floor visibility
- prefer corner-to-corner views with overlap
- avoid mixing different rooms

## What v2b Does

1. Segment floor with SegFormer.
2. Predict metric 3D points/normals with MoGe-2.
3. Estimate visible floor patch per image.
4. Optionally fuse views using DUSt3R camera alignment + MoGe floor points (`dust3r-scene`).
5. Return visible-floor estimate by default.
6. If `--room-impute` is set, also compute a Manhattan rectangle upper bound and use midpoint between visible and upper bound.

## Core CLI Options

- `--multiview-method dust3r-scene|single-image`:
  - `dust3r-scene` (default): fused scene floor patch.
  - `single-image`: per-image fallback fusion only.
- `--dust3r-iters N`:
  DUSt3R global alignment iterations (default `120`).
- `--room-impute`:
  optional rectangle completion for near-camera missing corners.
- `--debug-dir PATH`:
  save visual diagnostics.

## Debug Artifacts

With `--debug-dir`, you get:

- per-image overlays:
  - `per_image/*/floor_overlay.jpg`
  - `per_image/*/floor_projection.png`
- multiview scene overlays:
  - `multiview/dust3r-scene/scene_pose_moge_floor/scene_floor_topdown.png`
  - `multiview/dust3r-scene/scene_pose_moge_floor/scene_floor_components.png`
  - `multiview/dust3r-scene/scene_pose_moge_floor/reproject_view_*.png`
  - `multiview/dust3r-scene/scene_pose_moge_floor/reproject_cross_*.png`
  - `multiview/dust3r-scene/scene_pose_moge_floor/reproject_boundary_*.png`
  - `multiview/dust3r-scene/scene_pose_moge_floor/reproject_gallery.png`
  - `multiview/dust3r-scene/scene_pose_moge_floor/reproject_stitched_mosaic.png` (shared floor atlas in fused floor coordinates)
  - `multiview/dust3r-scene/scene_pose_moge_floor/reproject_stitched_floor_overlay.png` (top=actual photo views with fused overlays, bottom=shared atlas)
  - `multiview/dust3r-scene/scene_pose_moge_floor/scene_viewer.html`
  - `multiview/dust3r-scene/scene_pose_moge_floor/scene_all_points.ply`
  - `multiview/dust3r-scene/scene_pose_moge_floor/scene_floor_points.ply`
  - `multiview/dust3r-scene/scene_pose_moge_floor/scene_camera_centers.ply`

## Notes

- Default output is visible-floor area (no room-corner imputation).
- Interactive JSON saves under `v2-pipeline/runs/run_*/v2b_result.json` so sample datasets stay clean.
- Model cache is under `~/.cache/cv_pipeline/models/`.
