# Estimating Room Square Footage from Listing Photos

NYC rental listings frequently omit square footage, and when they include it, the number covers the whole unit — never individual rooms. This document records the computer-vision research behind SleepEasy's room-size estimation: the approaches tried, a 23-configuration benchmark, and a licensing audit of commercially-usable alternatives.

## Problem

Given 1–N professional listing photos of a room, estimate the room's floor area in square feet. Constraints that make this hard:

- **No camera metadata.** Listing photos are stripped of EXIF; focal length and sensor size are unknown.
- **Wide-angle distortion.** Real-estate photography uses wide lenses that exaggerate space.
- **No scale anchor.** Nothing in the scene has a known size; monocular depth must be *metric*, not relative.
- **Partial views.** A single photo rarely sees the whole floor.

## Dataset

- 137 StreetEasy listings collected (3,092 photos), via a Playwright-based collector with a web curation UI (`sqft-from-photos/sample-collection/`)
- 23 listings have ground-truth unit square footage (486 photos), ranging 461–1,800 sqft
- One benchmark room with known **room-level** ground truth: `listing_001`'s living room/kitchen, **266 sqft** (unit total 770 sqft) — the primary quantitative target throughout. Quantitative claims below are n=1; treat them as a development signal, not a validated accuracy distribution.

## Approaches

### v1 — Classical SfM + metric depth (archived)

COLMAP-style structure-from-motion with metric-depth scale recovery, deployed on RunPod (`sqft-from-photos/cv-pipeline/`). Worked end-to-end but was heavy, slow, and brittle on sparse listing photo sets (professional photos are too few and too wide-baseline for reliable feature matching).

### v2a — DUSt3R + Depth-Anything-V2

DUSt3R's learned multi-view reconstruction removes the need for classical feature matching. Failure mode: with **no floor segmentation**, non-floor geometry (counters, furniture, walls) inflated the convex hull — **118% overestimate** on the benchmark room. Deprecated.

### v2b — Segmentation + metric monocular geometry (shipped)

The architecture that ships in `selfhost/`:

1. **Floor segmentation** — SegFormer-B5 (ADE20K classes: floor, rug)
2. **Metric geometry** — MoGe-2 (ViT-L) recovers metric point maps, surface normals, and camera intrinsics from a single image
3. **Floor plane** — RANSAC plane fit over segmented floor points (typical residual 0.009–0.010 m)
4. **Area** — visible-floor polygon area on the fitted plane; per-image estimates fused across photos
5. **Multi-photo mode** — DUSt3R aligns cameras across photos of the same room; MoGe floor geometry is fused in the shared frame (alpha-shape boundary), capturing floor area no single photo sees

## Benchmark: 23 configurations (`v3_benchmakrs.py`)

A benchmark harness sweeps both pipelines across segmentation, depth, plane-fit, boundary, and scale-anchoring variants on the benchmark room (true 266 sqft). Highlights:

| Track | Configuration | Sqft | Error | Runtime |
|---|---|---:|---:|---:|
| Multi-view | **DUSt3R pose + MoGe floor (shipped default)** | **251.6** | **5.4%** | 23.0s |
| Multi-view | OneFormer segmentation variant | 252.0 | 5.3% | 23.3s |
| Multi-view | MASt3R pose variant | 248.5 | 6.6% | 27.3s |
| Multi-view | UniDepth-v2 scale anchor | 249.7 | 6.1% | 26.6s |
| Single-image | **SegFormer + MoGe (shipped fallback)** | **213.2** | **19.9%** | 17.6s |
| Single-image | OneFormer + MoGe | 213.8 | 19.6% | 16.9s |
| Single-image | UniDepth-v2 depth | 234.5 | 11.8% | 10.2s |
| Single-image | concave-adaptive boundary | 278.3 | 4.6% | 12.3s |
| Single-image | Depth-Anything-V2 metric | 417.1 | 56.8% | 8.6s |
| Single-image | ZoeDepth metric | 193.5 | 27.3% | 7.1s |

Takeaways:

- **Multi-view camera fusion is the accuracy unlock** — every DUSt3R-pose variant lands within 5–7% while standard single-image variants sit around 20% low (they only measure *visible* floor).
- **Metric depth model choice dominates single-image error.** MoGe-2 and UniDepth-v2 are usable; Depth-Anything-V2-metric and ZoeDepth are not, on this domain.
- The single-image concave-adaptive boundary variant (4.6%) effectively hallucinates plausible unseen floor; promising but untrusted at n=1.

## Licensing audit: a commercially-usable pipeline?

The best pipeline has a licensing problem: DUSt3R is **CC BY-NC-SA** (non-commercial), SegFormer carries a research-use restriction, and SuperPoint/LightGlue (the standard learned matcher) is also restricted. A dedicated evaluation (`sqft-from-photos/v2-pipeline/commercial_viable_paths/`) asked: what accuracy can MIT-licensed components alone achieve?

Spec: OneFormer (MIT) for segmentation, MoGe-2 (MIT) for geometry, and MoGe-intrinsics-based pose estimation with classical ORB/SIFT matching replacing DUSt3R.

| Path | Sqft | Error |
|---|---:|---:|
| Single-image: OneFormer + MoGe-2 | 213.8 | 19.6% |
| Multi-view: MoGe pose + ORB (scale-free) | 187.9 | 29.4% |
| Multi-view: MoGe pose + ORB | 166.0 | 37.6% |
| Multi-view: MoGe pose + ORB/SIFT hybrid | 161.4 | 39.3% |

**Conclusion: commercially-safe multi-view is blocked on pose estimation.** Classical matchers collapse on wide-baseline, low-texture interior photos — the best candidate kept only 9 of 283 matches as pose inliers. Every MIT-only multi-view path scored *worse* than just using one photo. The viable commercial offering today would be single-image-only at ~20% error; the 5% multi-view experience requires the non-commercial DUSt3R path. This is why SleepEasy ships as a free, self-hosted, non-commercial tool.

## Browser deployment probes

To test whether the backend could be eliminated entirely, MoGe was exported to ONNX and run in-browser (`sqft-from-photos/backend-local/ARCHITECTURE_STATUS.md`):

- MoGe-vits on WebGPU: **457 ms/image** — viable
- MoGe-vits on WASM: 5.98 s/image — marginal
- COLMAP-class SfM in browser: not viable

A browser-only single-image estimator is feasible; multi-view still needs local GPU compute.

## Status

The project ships the v2b architecture: DUSt3R multi-view by default on CUDA machines, single-image SegFormer+MoGe fallback everywhere else (verified end-to-end on Apple Silicon via MPS). Future directions, recorded for completeness: label room-level ground truth for the remaining 22 collected listings to harden the n=1 benchmark; investigate the concave-adaptive boundary as a single-image upgrade; revisit commercial multi-view if a permissively-licensed learned matcher emerges.
