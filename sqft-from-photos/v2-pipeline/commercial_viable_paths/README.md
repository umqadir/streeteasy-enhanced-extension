# Commercial-Viable Paths

This folder contains a commercial-oriented eval runner for the historical `listing_001` case.

## What It Runs

- `single_image_oneformer_moge`
  - Segmentation: `shi-labs/oneformer_ade20k_swin_large` (MIT)
  - Geometry/depth: `Ruicheng/moge-2-vitl-normal` (MIT)
  - Fusion: `estimate_v2b` single-image mode (`--multiview-method single-image`)
- Multiview candidates (all `moge-pose`, no DUSt3R):
  - `multiview_moge_pose_orb` (`allow_scale=false`)
  - `multiview_moge_pose_orb_allow_scale` (`allow_scale=true`)
  - `multiview_moge_pose_orb_sift_hybrid` (`allow_scale=false`)
  - `multiview_moge_pose_orb_sift_hybrid_allow_scale` (`allow_scale=true`)

The runner selects the better multiview candidate and writes `multiview_best.json`.

## What It Explicitly Avoids

- `SegFormer` path in `estimate_v2b` (license includes non-commercial use limitation)
- `DUSt3R` multiview path (CC BY-NC-SA)
- `LightGlue+SuperPoint` matcher path (SuperPoint is restrictive/non-commercial)

## No Duplicate Large Downloads

The runner sets:

- `HF_HUB_OFFLINE=1`
- `TRANSFORMERS_OFFLINE=1`

and uses `local_files_only=True` for OneFormer, so it reuses local cache only and fails fast if assets are missing.

## Run

From `v2-pipeline`:

```bash
uv run python commercial_viable_paths/run_commercial_listing001.py
```

Optional:

```bash
uv run python commercial_viable_paths/run_commercial_listing001.py \
  --listing-dir ../sample-collection/clean_set_export/photos/listing_001 \
  --image-names photo_00.jpg photo_01.jpg \
  --known-room-sqft 266
```

Outputs are written under:

- `v2-pipeline/commercial_viable_paths/runs/run_YYYYMMDDTHHMMSSZ/`
