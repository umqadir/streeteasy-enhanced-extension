# Commercial-Viable Listing_001 Evaluation

- Timestamp (UTC): `2026-02-27T02:19:34.264937+00:00`
- Listing dir: `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos/sample-collection/clean_set_export/photos/listing_001`
- Images: `photo_00.jpg, photo_01.jpg`
- Reference room sqft: `266.0`

## Spec Used

- Segmentation: `shi-labs/oneformer_ade20k_swin_large` (MIT)
- Depth/geometry: `Ruicheng/moge-2-vitl-normal` (MIT)
- Single-image fusion: `v2b` per-image path (`--multiview-method single-image`)
- Multiview pose: `moge-pose`
- Multiview candidates:
  - `ORB` (`allow_scale=false`)
  - `ORB` (`allow_scale=true`)
  - `ORB+SIFT hybrid` (`allow_scale=false`)
  - `ORB+SIFT hybrid` (`allow_scale=true`)
- Excluded from this spec: `SegFormer`, `DUSt3R`, `LightGlue+SuperPoint`

## Results

| Path | Sqft | 90% CI | Runtime (s) | Abs Error (sqft) | Method |
|---|---:|---:|---:|---:|---|
| single_image_oneformer_moge | 213.8 | [146.4, 235.2] | 19.56 | 52.2 | `per-image-fusion` |
| multiview_moge_pose_orb | 166.0 | [162.7, 213.8] | 16.93 | 100.0 | `moge-pose+moge-floor-visible` |
| multiview_moge_pose_orb_allow_scale | 187.9 | [162.7, 213.8] | 20.03 | 78.1 | `moge-pose+moge-floor-visible` |
| multiview_moge_pose_orb_sift_hybrid | 161.4 | [161.4, 213.8] | 21.49 | 104.6 | `moge-pose+moge-floor-visible` |
| multiview_moge_pose_orb_sift_hybrid_allow_scale | 161.4 | [161.4, 213.8] | 20.77 | 104.6 | `moge-pose+moge-floor-visible` |

Best multiview candidate: `multiview_moge_pose_orb_allow_scale`
- JSON: `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos/v2-pipeline/commercial_viable_paths/runs/run_20260227T021755Z/multiview_moge_pose_orb_allow_scale.json`
- Debug dir: `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos/v2-pipeline/commercial_viable_paths/runs/run_20260227T021755Z/debug/multiview_moge_pose_orb_allow_scale`

## Best Multiview Pose Diagnostics

- matcher: `orb`
- n_raw_matches: `283`
- n_3d_pairs: `283`
- n_inliers: `9`
- rmse_m: `0.0369758917087625`
