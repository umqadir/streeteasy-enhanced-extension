Note: operational “how to run this on RunPod / how to evaluate / how to sweep configs” is in `../../README.md`.

Below is a technical research + implementation guide for a **photo-only** pipeline that estimates apartment square footage (sqft) **without EXIF, without floor plans, and without domain fine-tuning at v1**, while still producing **a best estimate + calibrated uncertainty** and giving you clear iteration forks.

---

## 1) Problem reality check: what *is* and *isn’t* identifiable from photos

With only RGB photos, **absolute scale is fundamentally ambiguous** unless you inject *some* source of metric information (even a prior). Multi-view geometry (SfM/MVS) can recover structure, but only **up to a similarity transform** (scale is unknown). Your constraints (no EXIF, partial-room photos, multi-room views, no floor plans) mean the system must rely on:

* **Zero-shot metric depth / geometry foundation models** (they attempt to output depth in meters even “in the wild”), and/or
* **Object-size anchors** (doors, counters, beds, appliances), and/or
* **Camera priors** (typical FoV / camera height distribution), and/or
* **Some weak supervision later** (using listing sqft labels for calibration/training).

So the practical goal is:

1. **Reconstruct as much 3D as possible** (multi-view when possible; single-view fallback when not),
2. **Recover metric scale** using zero-shot metric geometry models and/or anchors,
3. **Compute floor area** from the reconstructed geometry,
4. **Quantify uncertainty** from (a) reconstruction quality, (b) scale ambiguity, and (c) incomplete coverage.

---

## 2) Current state-of-the-art building blocks relevant to your constraints

### A. Metric monocular depth / geometry “foundation models” (zero-shot, no fine-tune required)

These matter because they’re the most direct way to get *meters* from RGB without EXIF.

* **Metric3D v2**: explicitly targets **zero-shot metric depth** across many camera models via a canonical camera-space transform and large-scale training. ([arXiv][1])
* **UniDepth**: “universal monocular metric depth estimation,” aiming to generalize across domains and predict metric structure from a single image. ([arXiv][2])
* **ZoeDepth**: combines relative + metric depth and is widely used as a strong practical baseline for metric depth inference. ([arXiv][3])
* **Depth Anything V2**: primarily a strong monocular depth model; the project also releases **metric depth variants** and emphasizes robustness/efficiency. ([arXiv][4])
* **MoGe (Microsoft)**: predicts monocular geometry products and explicitly mentions **camera FoV** prediction—very relevant when EXIF is missing. ([GitHub][5])

**Why this matters for sqft:** if you can get (even somewhat noisy) metric depth and/or FoV, you can scale reconstructions and compute areas.

---

### B. Multi-view reconstruction: classical SfM/MVS + modern matchers

When photos have overlap, multi-view is your best path to a stable floor area estimate.

* **COLMAP** remains the standard SfM/MVS pipeline and supports **intrinsics refinement** (“self-calibration”) during reconstruction; it also has an explicit `default_focal_length_factor` when priors are missing. ([COLMAP][6])
* **hloc (Hierarchical Localization)** provides a practical pipeline for retrieval + feature matching + SfM with learned features (SuperPoint/SuperGlue, etc.), and integrates well with COLMAP. ([GitHub][7])
* **LightGlue** is a fast, accurate learned matcher that improves the feasibility of learned matching in reconstruction pipelines. ([arXiv][8])

---

### C. Uncalibrated “geometric foundation models” for multi-view (useful when SfM struggles)

* **DUSt3R (CVPR 2024)**: dense reconstruction paradigm that can operate without known camera calibration or poses—very aligned with “no EXIF / unknown intrinsics.” ([arXiv][9])
* **MASt3R (ECCV 2024)**: builds on DUSt3R ideas to treat matching as a 3D task and reports improved matching accuracy vs DUSt3R. ([arXiv][10])

These are great “Plan B” engines when COLMAP/feature matching fails due to wide baselines, textureless walls, or inconsistent overlap.

---

### D. Layout / floorplan inference from images or point clouds

You may want layout priors when geometry is incomplete.

* **RoomFormer (CVPR 2023)**: transformer approach for floorplan reconstruction from a top-down density map of a point cloud. It’s most relevant **after you already have a point cloud**. ([GitHub][11])
* **Layout Anything (Dec 2025)**: a recent transformer for universal room layout estimation from a single image (good candidate for fallback/priors). ([arXiv][12])
* Classic room layout estimation line (e.g., RoomNet, etc.) is relevant mainly as a **shape prior** under Manhattan-world assumptions. ([arXiv][13])

---

### E. Dense fusion / meshing

If you have camera poses + depth maps, TSDF fusion is an effective way to get a cleaner surface/point cloud:

* **Open3D TSDF integration**: standard volumetric fusion toolchain. ([Open3D][14])

---

## 3) Recommended v1 architecture (no domain fine-tune): “Multi-view-first, metric-scale, floor-area extraction”

### Top-level strategy

For each apartment listing (set of interior photos):

1. **Build an overlap graph** between images (cheap retrieval → selective matching)
2. Attempt **global multi-view reconstruction** (poses + sparse cloud)
3. Infer **metric scale** using a metric depth model (and optionally object anchors)
4. Produce **dense geometry** (fuse depth maps)
5. Extract one or more **floor planes**, compute **2D footprint area**, sum across floors
6. Compute **uncertainty bounds** via an ensemble + Monte Carlo around the scale/coverage

This directly handles your realities:

* Multiple rooms per photo → global reconstruction can still work if overlap exists
* Partial-room photos → contribute partial geometry; uncertainty should widen
* No EXIF → COLMAP self-calibration + monocular FoV/depth models reduce dependence

---

## 4) Detailed implementation guide

### 4.1 Inputs and outputs

**Inputs**

* `images[]`: user-curated “relevant unit photos” (already excludes amenities/exteriors)
* Optional: user-provided weak room tags (not required, but can help)

**Outputs**

* `sqft_estimate`: best estimate (recommend median of posterior)
* `sqft_interval_90`: [p05, p95] bounds (or 80/90/95 as product needs)
* `confidence_score`: 0–1 summary score
* `diagnostics`: reconstruction + scaling + coverage metrics, and “which fallback path used”

---

### 4.2 Pre-processing (fast, high ROI)

1. **Normalize images**

   * Resize longest side (e.g., 1600–2000 px) for SfM/matching
   * Keep original resolution for depth if GPU allows
2. **Deduplicate / near-duplicate removal**

   * Perceptual hash + embedding similarity
3. **Compute a “matchability score”**

   * # keypoints (SIFT or SuperPoint), Laplacian blur score, extreme exposure
   * Flag close-ups of appliances if they have low overlap; keep them for semantics, but optionally exclude from SfM attempt

This reduces catastrophic SfM failures from low-feature images.

---

### 4.3 Automatic grouping without requiring room labels

Even though 1:1 room assignment is unrealistic, you can still improve robustness by forming an overlap graph.

**Recommended approach**

* Compute global image embeddings (e.g., DINOv2/CLIP-style) for retrieval
* For each image, take top-K nearest neighbors
* Only run expensive matching on those candidate pairs
* Build a graph where edge weight = #inlier matches (or match confidence)
* Use connected components (or community detection) to get clusters

Then attempt:

* **Global reconstruction** first (all images)
* If it fails or fragments → reconstruct per component and merge (or just compute area per component with overlap correction later)

This pattern mirrors the “retrieval + matching” philosophy popularized by hloc. ([GitHub][7])

---

### 4.4 SfM reconstruction (primary path)

#### Option A (recommended): hloc-style matching → COLMAP mapper

* Use a learned matcher (LightGlue or SuperGlue) for robustness and speed. ([arXiv][8])
* Feed matches into COLMAP for incremental SfM.

#### Option B (fallback): vanilla COLMAP pipeline

Useful if you want fewer dependencies and can tolerate lower success rates.

**Important COLMAP considerations with no EXIF**

* COLMAP has a `default_focal_length_factor` (commonly 1.2×image_width) when focal length is unknown. ([COLMAP][6])
* COLMAP by default tries to **refine intrinsics** (except principal point) during reconstruction; this can help but can also produce degenerate intrinsics if the problem is weakly constrained. ([COLMAP][15])

**Implementation defaults**

* Camera model: start with `SIMPLE_RADIAL` / `OPENCV` depending on distortion levels
* Share intrinsics across images if you believe photos came from similar devices (often true within a listing, but not guaranteed)
* Keep principal point fixed initially; optionally refine at the end if the reconstruction is stable ([COLMAP][15])

**Success criteria** (store these for diagnostics)

* fraction of images registered
* # sparse points
* reprojection error stats
* number of connected components / sub-models

---

### 4.5 Metric scale estimation (the critical step)

After SfM you have:

* camera poses, intrinsics (estimated), sparse 3D points
* but scale is arbitrary

You need a **scale factor `s`** to map SfM units → meters.

#### Core idea: align SfM depths with metric monocular depth predictions

1. Run a metric depth model per image (start with Metric3D v2 or UniDepth; optionally ensemble). ([arXiv][1])
2. For each registered image:

   * For each SfM 3D point visible in that image, compute its depth `z_sfm` in the camera frame
   * Sample predicted metric depth `z_pred` from the depth map at the corresponding pixel
3. Fit a robust scale `s` such that: `z_pred ≈ s * z_sfm`

   * Use RANSAC or Huber regression (depth maps have outliers: mirrors/windows/TVs)
4. Aggregate across images:

   * Use weighted median of per-image scales (weights = #inliers, inverse residual)
5. Compute scale uncertainty from:

   * residual distribution
   * inter-image scale variance
   * model ensemble variance (if you run multiple depth models)

**Why Metric3D v2 / UniDepth / MoGe are especially relevant**

* Metric3D v2 targets metric ambiguity across camera models. ([arXiv][1])
* UniDepth targets cross-domain metric generalization. ([arXiv][2])
* MoGe claims to predict camera FoV, which can stabilize metric interpretation when EXIF is missing. ([GitHub][5])

#### Add optional object anchors (improves bounds, not required for v1)

If you can detect **doors**, **kitchen counters**, **beds**, you can create independent scale hypotheses:

* Detect object mask (GroundingDINO + SAM or any detector/segmenter you trust)
* Estimate object plane + depth around it
* Compare recovered metric size vs known priors (e.g., typical door height distribution)
* Use as an additional likelihood term in the scale posterior

In v1, treat this as **a scale sanity-check + uncertainty reducer** rather than the only scale source.

---

### 4.6 Dense geometry fusion (poses + metric depth → fused cloud/mesh)

Once you have poses and a metric scale:

* Convert each depth map into a point cloud in world coordinates
* Fuse depth into a TSDF volume to reduce noise and fill small gaps

Use Open3D’s TSDF integration as the default dense fusion engine. ([Open3D][14])

**Practical notes**

* You’re using predicted (not sensor) depth, so expect noisier edges.
* Mask out likely invalid depth regions (windows, mirrors) using heuristics (high residuals during scale fit; or semantic segmentation).

---

### 4.7 Floor plane extraction and footprint area computation

Goal: compute apartment floor area (possibly multi-floor).

**Step A: Identify floor plane(s)**

* From fused point cloud:

  * RANSAC plane detection to find large planes
  * Find planes that are near-horizontal after orientation alignment
* COLMAP has Manhattan-world alignment tools that can help align axes. ([COLMAP][15])

**Step B: For each floor plane**

1. Extract points within a small distance threshold from the plane
2. Project to 2D coordinates on that plane
3. Build a 2D occupancy / density map (grid)
4. Extract boundary polygon:

   * Alpha shape / concave hull is usually better than convex hull
   * Fill small holes (morphological closing)
5. Compute polygon area in m² → convert to sqft

**Step C: Multi-floor handling**

* If you detect two dominant horizontal planes separated significantly in height, compute area per plane and sum.
* Confidence should drop unless you have enough coverage on both planes.

**Important: coverage-aware area**
Listing photos rarely cover closets, hallways, etc. You should explicitly track:

* observed floor coverage ratio (how much floor surface you actually reconstructed)
* boundary “completeness” score (how closed/continuous is the footprint)

Use these to widen bounds.

---

## 5) Uncertainty & confidence: produce bounds that are meaningful (not just a heuristic score)

You want:

* best estimate
* confidence metrics
* ideally bounds

### 5.1 Define a simple posterior over sqft

Produce a distribution by sampling uncertainty sources:

**Uncertainty sources to sample**

1. **Scale factor `s`**: sample from robust-fit residuals / bootstrap across images
2. **Depth model choice**: sample across an ensemble (Metric3D v2 / UniDepth / ZoeDepth / Depth Anything metric) ([arXiv][1])
3. **Floor boundary extraction settings**: alpha radius / grid resolution jitter (small effect but helps avoid brittle boundaries)
4. **Component fragmentation**: if reconstruction splits into components, sample plausible overlap correction (see below)

For each sample, recompute sqft → build empirical quantiles.

### 5.2 Component fragmentation correction (avoid double counting)

If SfM gives multiple sub-models:

* Compute sqft per component footprint
* Estimate overlap probability between components using:

  * image similarity edges across components
  * similarity of wall-plane orientations / heights
* In v1, simplest conservative approach:

  * **Lower bound**: max(component areas)
  * **Upper bound**: sum(component areas)
  * **Point estimate**: weighted blend based on overlap score

Then once you improve merging, you can tighten.

### 5.3 Produce a single confidence score (0–1) from interpretable factors

Recommend a monotonic mapping of:

* SfM quality: % images registered, reprojection error, #points
* Scale quality: #scale inliers, scale variance across images
* Floor plane quality: inlier ratio, number of planes, stability under resampling
* Coverage: estimated observed floor area fraction

This confidence score should correlate with interval width and observed error on a validation set.

---

## 6) Fallback pipelines (must-have in production)

### Fallback 1: “Depth-only + layout prior” (when SfM fails)

Trigger when:

* <3 images register
* overlap graph is too sparse
* reconstruction is unstable/degenerate

Pipeline:

1. Run a metric geometry model that also helps with camera assumptions (MoGe / UniDepth). ([GitHub][5])
2. Segment floor region (general semantic segmentation)
3. Backproject floor pixels to 3D using predicted depth (+ FoV if available)
4. Fit a floor plane; compute visible floor patch area
5. Use a layout estimator as a shape prior to extrapolate to full room:

   * Layout Anything is a candidate modern single-image layout model ([arXiv][12])
6. If multiple images exist, aggregate per-image estimated room areas with clustering-by-similarity and de-duplication.

This gives a **wide but honest** interval when multi-view geometry is impossible.

---

### Fallback 2: DUSt3R/MASt3R-based reconstruction (when classical SfM/matching struggles)

Trigger when:

* SfM fails due to textureless walls / wide baselines / unknown intrinsics issues

Pipeline:

1. Use DUSt3R to get dense correspondences / reconstruction without calibration assumptions. ([arXiv][9])
2. Optionally use MASt3R concepts/models for improved matching robustness. ([arXiv][10])
3. Still recover metric scale using the same scale-alignment approach (fit DUSt3R scale to metric depth outputs)
4. Fuse depth/pointmaps → floor extraction → sqft

This is a structural alternative that may dramatically improve “hard” listings.

---

## 7) Evaluation plan (do this before optimizing anything)

Even if you won’t train on domain data at v1, you still need **domain evaluation**.

### 7.1 Build a validation dataset

* Use listing-reported square footage as weak ground truth (acknowledge noise)
* Stratify by:

  * unit size buckets
  * number of photos
  * photo quality
  * presence of wide-angle distortion
  * studio vs 1BR/2BR etc (if known)

### 7.2 Metrics

* MAE (sqft)
* MAPE (percentage error)
* % within ±10%, ±15%, ±20%
* **Calibration of bounds**: empirical coverage of p05–p95 interval should be near 90%

### 7.3 Instrumentation (critical)

For every run store:

* which path ran (multi-view, fallback 1, fallback 2)
* all quality diagnostics
* final interval width

This lets you build a “failure mode map.”

---

## 8) Iteration roadmap & decision points (what to try next, based on diagnostics)

### Decision Point A: Is multi-view SfM succeeding often enough?

* If **SfM success rate is high** (e.g., most listings produce a stable reconstruction):

  * Focus on improving **scale estimation** and **floor boundary extraction**
* If **SfM fails frequently**:

  * Move matching to learned matchers (LightGlue/hloc) ([GitHub][7])
  * Add DUSt3R/MASt3R fallback ([CVF Open Access][16])
  * Increase robustness: filter non-overlapping close-ups from SfM

### Decision Point B: Is scale the dominant error?

Diagnose by: large variance in fitted `s`, inconsistent depth alignment residuals.

* Try:

  * Switch metric depth model (Metric3D v2 vs UniDepth vs ZoeDepth) ([arXiv][1])
  * Add MoGe FoV prediction to stabilize intrinsics when EXIF is missing ([GitHub][5])
  * Add object anchors (doors/counters) as weak scale constraints

### Decision Point C: Are you under-estimating because of incomplete floor visibility?

Diagnose by: low observed floor coverage ratio + small footprint polygons.

* Try:

  * Add layout priors (Layout Anything / other layout estimators) ([arXiv][17])
  * Add a “coverage-to-unobserved-area” probabilistic model (even a hand-built prior initially)

### Decision Point D: Confidence intervals not calibrated?

Diagnose by: p05–p95 contains true sqft much less/more than 90%.

* Fix by:

  * Post-hoc calibration (conformal-style) using a held-out validation set
  * Add more uncertainty sources into sampling (scale, boundary extraction, fragmentation)

---

## 9) Path to improvement with domain-specific training data (most efficient next steps)

You asked specifically: start zero-shot, but define the **most efficient** training path *if/when needed*.

### Step 1 (low effort, high ROI): calibrate scale globally (not “training” a new model)

Once you have a few thousand labeled listings:

* Fit a simple calibration mapping per depth model:

  * `sqft_corrected = a * sqft_raw + b`
* Or calibrate the scale factor `s` distribution by a multiplicative correction
  This often yields meaningful gains with minimal engineering risk.

### Step 2: add a learned “sqft-from-images” prior as a backstop (multi-instance regression)

When geometry fails, a learned prior can prevent catastrophic misses.

* Use frozen image encoders (CLIP/DINO embeddings) + MIL pooling over photos
* Train a lightweight regressor to predict sqft + uncertainty
* Blend with geometry output (Bayesian model averaging)

This doesn’t replace geometry; it catches failure cases.

### Step 3: fine-tune a metric depth / layout model (only if diagnostics say it’s necessary)

Trigger this only if:

* scale variance remains huge even after calibration/anchors, or
* your domain has strong distribution shift (extreme wide-angle, heavy staging patterns)

Efficient paths:

* Fine-tune only a small head / LoRA adapters on depth or FoV prediction
* Train with weak supervision:

  * consistency between multi-view geometry and predicted depth
  * listing sqft as a global constraint (very weak; treat carefully)

### Step 4: active learning loop

Use your own confidence metrics:

* sample high-uncertainty listings for manual review or for collecting more photos
* use corrected labels to improve calibration/training efficiently

---

## 10) Suggested engineering deliverables (what your engineer should build)

### Milestone 1: “Geometry v0” (1 listing → sqft)

* Preprocess + overlap graph
* COLMAP reconstruction
* Metric depth inference (pick 1 model first: Metric3D v2 or UniDepth)
* Scale alignment
* Floor plane extraction + area calc
* Output JSON with sqft + diagnostics

### Milestone 2: “Production robustness”

* Learned matching (hloc + LightGlue) ([GitHub][7])
* TSDF fusion in Open3D ([Open3D][14])
* Multi-component handling (bounds)
* Ensemble depth models (2–3)

### Milestone 3: “Uncertainty you can trust”

* Monte Carlo posterior sampling
* Interval calibration evaluation
* Confidence score mapping + dashboard

### Milestone 4: “Fallbacks”

* Depth-only fallback (layout-prior)
* DUSt3R/MASt3R fallback path ([CVF Open Access][16])

---

## 11) Practical model defaults (a strong starting configuration)

**Default v1 stack**

* SfM: COLMAP + learned matching (hloc) ([GitHub][7])
* Matcher: LightGlue ([arXiv][8])
* Metric depth: Metric3D v2 (primary), UniDepth (secondary) ([arXiv][1])
* Dense fusion: Open3D TSDF ([Open3D][14])
* Layout prior (fallback): Layout Anything ([arXiv][17])

**Why these defaults**

* They directly address: no EXIF, wide variability in photos, and need for metric scale.

---

If you want, I can also provide:

* a concrete **module-by-module repo skeleton** (Python package layout),
* a proposed **JSON schema** for diagnostics + outputs,
* and a minimal set of **unit tests** (geometry sanity checks: plane normal stability, scale-fit residual thresholds, footprint polygon validity).

[1]: https://arxiv.org/abs/2404.15506?utm_source=chatgpt.com
[2]: https://arxiv.org/abs/2403.18913?utm_source=chatgpt.com
[3]: https://arxiv.org/abs/2302.12288?utm_source=chatgpt.com
[4]: https://arxiv.org/abs/2406.09414?utm_source=chatgpt.com
[5]: https://github.com/microsoft/MoGe?utm_source=chatgpt.com
[6]: https://colmap.github.io/cli.html
[7]: https://github.com/cvg/Hierarchical-Localization?utm_source=chatgpt.com
[8]: https://arxiv.org/abs/2306.13643?utm_source=chatgpt.com
[9]: https://arxiv.org/abs/2312.14132?utm_source=chatgpt.com
[10]: https://arxiv.org/abs/2406.09756?utm_source=chatgpt.com
[11]: https://github.com/ywyue/RoomFormer?utm_source=chatgpt.com
[12]: https://arxiv.org/html/2512.02952?utm_source=chatgpt.com
[13]: https://arxiv.org/abs/1703.06241?utm_source=chatgpt.com
[14]: https://www.open3d.org/html/tutorial/t_reconstruction_system/integration.html?utm_source=chatgpt.com
[15]: https://colmap.github.io/faq.html
[16]: https://openaccess.thecvf.com/content/CVPR2024/papers/Wang_DUSt3R_Geometric_3D_Vision_Made_Easy_CVPR_2024_paper.pdf?utm_source=chatgpt.com

[17]: https://arxiv.org/pdf/2512.02952?utm_source=chatgpt.com
