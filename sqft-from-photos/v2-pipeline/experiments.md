# Experiment Log: sqft-from-photos v2

Each variant is a standalone script (`estimate_v2a.py`, `estimate_v2b.py`, ...).
Run any variant with: `uv run python estimate_v2X.py <folder>`

## Pipeline Variants

### v2a: DUSt3R + Depth-Anything-V2 → `estimate_v2a.py`

**Approach**: Full point cloud from DUSt3R, metric scale from Depth-Anything-V2, RANSAC floor plane from ALL points (no segmentation).

**Models**:
- DUSt3R ViT-Large (2.1 GB) — multi-view 3D reconstruction
- Depth-Anything-V2 Metric ViT-L hypersim (1.3 GB) — metric depth only, no intrinsics

**Key limitation**: No floor segmentation. RANSAC finds floor plane from entire point cloud, so non-floor points near the plane (walls at floor level, furniture bases, countertops) inflate the area.

| Listing | Ground Truth | Estimate | Error | Notes |
|---------|-------------|----------|-------|-------|
| listing_001 (6 imgs, multi-room) | 770 sqft | 1682 sqft | +118% | Photos span entire apartment, not one room. Floor score 0.167 (low). |

**Verdict**: Pipeline runs end-to-end (~105s on MPS). But (a) needs room-specific photo selection, (b) no segmentation → massive overestimate from non-floor points, (c) Depth-Anything doesn't predict intrinsics.

---

### v2b: SegFormer + MoGe-2 + DUSt3R → `estimate_v2b.py`

**Approach**: Follow the "visible floor patch" guide. Segment floor pixels first, then use MoGe-2 for metric depth+intrinsics+normals, backproject only floor pixels, fit plane, compute area. DUSt3R for multi-view consistency.

**Models**:
- SegFormer-B5 ADE20K (85M) — floor segmentation (class 3=floor, 28=rug)
- MoGe-2 ViT-L normal (1.2 GB) — metric depth + intrinsics + normals + 3D points
- DUSt3R ViT-Large (2.1 GB) — multi-view alignment (optional)

**Key improvements over v2a**:
1. Floor segmentation gates everything — only floor pixels enter the pipeline
2. MoGe-2 predicts intrinsics (no EXIF needed) + surface normals (floor detection prior)
3. Planarity check: floor points should form a clean plane; if not, widen uncertainty
4. Per-image floor area works without multi-view (single-image viable)
5. Multi-view adds consistency, not required for basic estimate

**Results**:

Note: v2b estimates **visible floor area** (exposed floor in each photo), not total room area. This is by design — the guide explicitly defines the target as "the exposed floor patch that a top-down camera would see."

| Listing | Ground Truth | Estimate | CI | Time | Notes |
|---------|-------------|----------|-----|------|-------|
| listing_001 (3 imgs) | 770 sqft (full apt) | 188 sqft | [2-235] | 122s | Visible floor only. photo_00: 213sqft (34% floor), photo_01: 162sqft (30% floor), photo_02: 2sqft (1% floor — likely close-up). Low plane residuals (0.009-0.010m) = good planarity. |

**Diagnostics**:
- Floor segmentation is working: 30-34% of pixels classified as floor in room photos, 1% in close-up
- Plane fit quality is high: residuals 0.009-0.010m (sub-centimeter)
- Normal filtering (upward-facing normals AND segmentation mask) correctly gates floor points
- MoGe-2 metric depth on MPS works but triggers autocast warning (non-fatal, falls back to float32)

**Verdict**: Pipeline geometry is sound (low residuals, clean segmentation). The 188 sqft result represents visible floor in 3 photos of a multi-room apartment — not comparable to the 770 sqft ground truth which is total apartment area. Need to test with same-room photos where visible floor area can be sanity-checked.

---

## Model Inventory

All cached at `~/.cache/cv_pipeline/models/`:

| Model | Type | Size | Vendor Repo | Checkpoint |
|-------|------|------|-------------|------------|
| DUSt3R ViT-L | Multi-view recon | 2.1 GB | vendor/dust3r | checkpoints/dust3r/ |
| MASt3R ViT-L metric | Multi-view recon | 2.6 GB | vendor/mast3r | checkpoints/mast3r/ |
| Depth-Anything-V2 ViT-L | Metric depth only | 1.3 GB | vendor/depth-anything-v2 | checkpoints/ |
| MoGe-2 ViT-L normal | Depth+intrinsics+normals | 1.2 GB | vendor/moge | checkpoints/moge/ |
| UniDepth V1 ViT-L14 | Depth+intrinsics | 1.3 GB | vendor/unidepth | checkpoints/unidepth/ |
| Metric3D V2 ViT-S | Metric depth (CUDA only) | 143 MB | vendor/metric3d | checkpoints/metric3d/ |
| SegFormer-B5 ADE20K | Floor segmentation | ~85 MB | HuggingFace | (download on first use) |

## Key Learnings

- StreetEasy listings have 10-26 photos spanning multiple rooms. Must select same-room photos.
- DUSt3R works on MPS but is slow (~40s for global alignment on 4 images).
- Without floor segmentation, RANSAC picks up too many non-floor points.
- MoGe-2 > UniDepth for indoor metric depth (26% lower relative error, works on MPS).
- MoGe-2 surface normals can serve as a secondary floor prior (normals pointing up = floor).
- v2b visible-floor estimates are fundamentally different from total room area — they measure exposed floor in each photo.
- Floor segmentation + normal filtering is a strong combination: 30-34% floor in room photos, <1% in close-ups.
- Plane residuals of ~0.01m indicate MoGe-2 metric depth is geometrically consistent for indoor floor planes.
