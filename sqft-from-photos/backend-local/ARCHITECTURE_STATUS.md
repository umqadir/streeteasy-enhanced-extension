# Deployment Architecture Status (Commercial-Safe)

Date: 2026-02-17

## Scope

- Evaluation listing: `sample-collection/clean_set_export/photos/listing_001`
- Goal: determine viable commercial-safe deployment path for extension-compatible inference.

## Verified Findings

### 1) Single-image core model is browser-viable

Model family tested: MoGe-2 ONNX (`vits`, `vitb`, `vitl`)

- `vits` (`~134 MB`): browser WebGPU mean `~457 ms` (256x256), WASM `~5.98 s`
- `vitb` (`~400 MB`): browser WebGPU mean `~373 ms` (192x192), WASM `~6.64 s`
- `vitl` (`~1263 MB`): browser WebGPU mean `~642 ms` (160x160), WASM `~12.17 s`

All checkpoints executed successfully in headless Chrome via `onnxruntime-web`.

Legal note:
- The tested MoGe ONNX repos on Hugging Face do not currently expose explicit license metadata in the model repos themselves (no model card/license tag).
- Commercial usage should be cleared against upstream MoGe licensing and/or explicit maintainer confirmation for those ONNX checkpoints before store distribution.

Primary artifacts:
- `v2-pipeline/runs/browser_rd_20260217T220154Z/BROWSER_MODEL_SCALE_COMPARE.md`
- `v2-pipeline/runs/browser_rd_20260217T220154Z/web_probe_webgpu.json`
- `v2-pipeline/runs/browser_rd_20260217T220154Z/web_probe_wasm.json`

### 2) COLMAP remains non-viable on listing_001 for this photo regime

Re-check done with both:
- `exhaustive`
- `lightglue`

Both failed mapper initialization and dropped to depth-only fallback.

Artifacts:
- `v2-pipeline/runs/browser_rd_20260217T220154Z/colmap_recheck_summary.json`
- `v2-pipeline/runs/browser_rd_20260217T220154Z/colmap_exhaustive.json`
- `v2-pipeline/runs/browser_rd_20260217T220154Z/colmap_lightglue.json`

## Implications

### For extension (Chrome Store) local-only path

A feasible local browser inference path exists for **single-image floor patch estimation** using MoGe ONNX + WebGPU.

- Recommended browser-first default: `moge-2-vits-normal-onnx`
- Recommended fallback: WASM only for compatibility (slow)

### For multi-view scene fusion

A robust commercial-safe replacement for DUSt3R is still unresolved in this codebase.

- Current non-commercial DUSt3R path is technically strong but license-blocked for commercial distribution.
- COLMAP fallback is not robust enough on the target listing photos.

## Recommended near-term architecture

1. Ship browser-local single-image inference first:
- MoGe-2 `vits` ONNX + WebGPU
- Preserve current visual diagnostics UX pattern

2. Keep multi-view fusion behind a service boundary until replacement is proven:
- either server-side commercial model option
- or a new in-house commercial-safe multi-view alignment module

3. Add runtime capability detection in extension:
- WebGPU available -> run local model
- otherwise show graceful fallback path (WASM or backend)

## Open technical work

1. Browser memory profiling on real user hardware (not just headless environment).
2. Browser segmentation component strategy (license-clean, accurate floor boundaries).
3. Commercial-safe multi-view replacement benchmarking against current DUSt3R diagnostics standard.
