# Commercial-Viable Listing_001 Evaluation

- Timestamp (UTC): `2026-02-27T01:06:32.893406+00:00`
- Listing dir: `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos/sample-collection/clean_set_export/photos/listing_001`
- Images: `photo_00.jpg, photo_01.jpg`
- Reference room sqft: `266.0`

## Spec Used

- Segmentation: `shi-labs/oneformer_ade20k_swin_large` (MIT)
- Depth/geometry: `Ruicheng/moge-2-vitl-normal` (MIT)
- Single-image fusion: `v2b` per-image path (`--multiview-method single-image`)
- Multiview pose: `moge-pose`
- Multiview candidate A matcher: stock `ORB`
- Multiview candidate B matcher: `ORB+SIFT hybrid` (OpenCV)
- Excluded from this spec: `SegFormer`, `DUSt3R`, `LightGlue+SuperPoint`

## Results

| Path | Sqft | 90% CI | Runtime (s) | Abs Error (sqft) | Method |
|---|---:|---:|---:|---:|---|
| single_image_oneformer_moge | 213.8 | [146.4, 235.2] | 22.51 | 52.2 | `per-image-fusion` |
| multiview_moge_pose_orb | 166.0 | [162.7, 213.8] | 18.66 | 100.0 | `moge-pose+moge-floor-visible` |
| multiview_moge_pose_orb_sift_hybrid | 161.4 | [161.4, 213.8] | 18.26 | 104.6 | `moge-pose+moge-floor-visible` |

Best multiview candidate: `multiview_moge_pose_orb`
- JSON: `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos/v2-pipeline/commercial_viable_paths/runs/run_20260227T010533Z/debug/multiview_moge_pose_orb.json`
- Debug dir: `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos/v2-pipeline/commercial_viable_paths/runs/run_20260227T010533Z/debug/multiview_moge_pose_orb`

## Best Multiview Pose Diagnostics

- matcher: `orb`
- n_raw_matches: `283`
- n_3d_pairs: `283`
- n_inliers: `9`
- rmse_m: `0.03717766333380856`
