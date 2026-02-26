# Persistent Execution Plan

This document is the long-running execution contract for the `sqft-from-photos` effort.
It is designed to survive chat compactions and keep work moving until goals are fully met or truly blocked.

## Program Goals

1. Replace non-commercial multi-view reconstruction (DUSt3R/MASt3R) with a commercially viable multi-view alignment/reconstruction path that genuinely leverages multiple views for a better room floor layout.
2. Keep single-image estimation working, but treat it as a fallback; multi-view reconstruction quality is the primary bar.
3. Benchmark and iterate on `listing_001` until the multi-view bar is cleared with reproducible artifacts and diagnostics.
4. Only after (1) is cleared: optimize for browser/local deployment and extension integration.

## Non-Negotiable Success Criteria

1. Commercial viability is verified from primary source licensing terms (code + weights/checkpoints), not assumptions.
2. A multi-view path exists that produces a coherent shared scene (poses + consistent floor plane) and improves or at least stabilizes the fused floor layout versus per-image aggregation on `listing_001`.
3. Benchmarks include runtime and memory indicators (RSS and, when available, VRAM).
4. Diagnostics include visual cross-view verification:
   - per-view reprojection overlays (floor points + fused boundary)
   - a top-down floor atlas view
   - a numeric cross-view reprojection IoU summary
5. Browser feasibility is only pursued for candidates that pass (1)-(4), unless explicitly scoped as exploratory.

## Guardrails

1. Do not downgrade goals or success criteria without explicit user approval.
2. Treat ambiguous licensing as high risk until resolved with explicit model-card/license evidence.
3. Prefer minimal architecture complexity unless complexity yields measurable gains.
4. Keep baseline v2 behavior intact unless a replacement is demonstrably superior on legal + technical axes.

## Workstream Structure

### WS1: License + Compliance Audit
- Build allow/deny/uncertain matrix for all candidate models and auxiliary components.
- Track source links and date-verified status.

### WS2: Multi-View Reconstruction Replacement (Primary)
- Goal: a legitimate multi-view alignment/reconstruction backend to replace DUSt3R.
- Iterate on `listing_001` until scene coherence is visibly correct and metrics are acceptable.
- Candidate families (in order):
  - MoGe-2 pointmaps + learned matching + pose-graph alignment (no DUSt3R).
  - COLMAP (aggressively tuned) + metric depth scale, if it can be made to initialize reliably.
  - Additional view-graph / splatting / MVS approaches if above fail (e.g. AnySplat-class pipelines).

### WS3: Benchmark + Regression Harness
- Maintain a single authoritative benchmark runner scoped to `listing_001`.
- For each candidate backend, store:
  - timings + memory
  - reprojection IoU + inlier stats
  - a stable set of diagnostic images for human review

### WS4: Integration Recommendation
- Define deployable architecture options:
  - fully local browser
  - hybrid local + remote
  - local desktop backend + extension client
- Provide explicit tradeoffs and implementation order.

## Deadlock Definition

Work is considered deadlocked only if one of the following persists after at least two alternative attempts:

1. No commercially viable model can satisfy required outputs for a pipeline stage.
2. Browser runtime cannot execute required operators/models with acceptable latency/memory.
3. Required licensing terms are inaccessible/ambiguous and block legal go/no-go decisions.

If deadlocked:
- capture exact blocker,
- list attempts made,
- propose minimum external dependency or product decision needed to unblock.

## Iteration Log

### 2026-02-17 (Current Session)
- [done] Created persistent execution plan with non-negotiable success criteria.
- [in_progress] Build verified commercial viability matrix from primary sources.
- [pending] Run focused experiments for commercial-safe options (MoGe, COLMAP-assisted variants, simple alts).
- [pending] Move to browser inference path experiments using only passing candidates.

### 2026-02-17 (listing_001 targeted batch)
- [done] Constrained benchmark/probe scope to `sample-collection/clean_set_export/photos/listing_001`.
- [done] Re-ran COLMAP viability with both `exhaustive` and `lightglue` matchers on listing_001.
- [done] Confirmed both COLMAP runs fail mapper initialization and fall back to depth-only path.
- [done] Built and executed browser inference probes for MoGe-2 ONNX (`vits`) in headless Chrome.
- [done] Verified successful `onnxruntime-web` execution on both `webgpu` and `wasm` EPs after token-shape fix.
- [done] Extended browser probes to MoGe-2 ONNX `vitb` and `vitl` to map model-size scaling.
- [done] Captured consolidated browser scaling table and listing_001 summary docs under `v2-pipeline/runs/browser_rd_20260217T220154Z/`.
- [done] Wrote deployment architecture status doc: `backend-local/ARCHITECTURE_STATUS.md`.
- [done] Revalidated `v2-pipeline/browser-rd/run_all.sh` end-to-end after fixes (browser probes succeed; COLMAP still non-viable on listing_001).
- [done] Latest reproducible listing_001 run bundle: `v2-pipeline/runs/browser_rd_20260217T223055Z/` (auto-generated `SUMMARY.md`).
- [in_progress] Expand browser probes to additional commercial-safe candidates and capture side-by-side tradeoffs.
- [in_progress] Resolve license ambiguity for MoGe ONNX checkpoint repos (HF metadata missing explicit license tag/card).

## Next Actions Queue

1. Finalize allow/deny/uncertain licensing table with source citations.
2. Execute benchmark runs for commercial-safe candidates on known-room test case(s), preserving strict listing_001 comparability.
3. Store results in machine-readable and human-readable summaries.
4. Extend browser feasibility from MoGe-only to at least one additional commercial-safe model path.
5. Produce architecture recommendation for extension deployment (browser-only vs hybrid local backend vs remote fallback).
