# RunPod setup notes

This project assumes:

- **Network volume**: persistent storage for models + results (set `CVP_VOLUME`)
- **Container volume**: ephemeral storage for in-progress jobs (set `CVP_WORKDIR`)

Defaults:

- If `CVP_VOLUME` is unset, the code prefers `/runpod-volume`, then `/workspace`.
- If `CVP_WORKDIR` is unset, the code uses `/tmp/cv_pipeline_work`.

RunPod UI mapping:

- **Volume disk** = persistent storage (mount it at `/workspace` and/or set `CVP_VOLUME`)
- **Container disk** = ephemeral working disk (use this for `CVP_WORKDIR`)

## One-command bootstrap (recommended)

After you `git clone` the repo **into your mounted volume** (commonly `/workspace`), run:

```bash
bash cv-pipeline/scripts/runpod_bootstrap.sh
```

This will:

- install system deps (COLMAP)
- create a Python env via `uv` (reusing the image’s torch via `--system-site-packages`)
- install Codex + Claude Code CLIs into `"$CVP_VOLUME/tools/npm"` (persistent)
- write an env file at `"$CVP_VOLUME/cv_pipeline_env.sh"` for new shells

It does **not** download model weights (run `download_models.py` after).

## Do I need a custom container image?

Not required.

The repo is set up so you can use a stock CUDA/PyTorch RunPod image and run `runpod_bootstrap.sh`.
If you want faster cold-starts, a custom image can pre-install:

- `colmap` + system libs
- `uv` + project deps
- Node.js + `codex` / `claude`

Even with a custom image, keep **model weights and caches** on the **network volume** (`CVP_VOLUME`) so you don’t re-download.

## Can I run without a network volume?

Yes, but you lose persistence.

If you don’t attach a RunPod network volume, set:

```bash
export CVP_VOLUME=/workspace
```

Everything (model weights, caches, and run outputs) will be stored on the pod’s **container disk**. This works for experiments, but:

- If the pod is **terminated/recreated**, you’ll re-download models and lose run outputs.
- You’ll need a **large container disk** if you download multiple large checkpoints.

## 1) System deps (COLMAP)

You need a working `colmap` binary in the container.

Option A (Ubuntu/Debian, easiest):

```bash
bash cv-pipeline/scripts/runpod_setup_system.sh
```

If your base image doesn't have `apt-get`, install COLMAP via your image choice (recommended: start from a CUDA + Ubuntu image and install).

## 2) Python deps

From the repo root:

```bash
cd cv-pipeline
uv sync --extra gpu --extra sfm --extra depth --extra open3d
```

Notes:

- In most RunPod PyTorch images, `torch` is already installed. If so, `uv sync --extra gpu` may skip installing torch.
- If you hit CUDA / torch version issues, prefer the base image’s torch and remove torch from the extras.

For the full research plan (DUSt3R/MASt3R VRAM profiling), also install:

```bash
uv sync --extra gpu --extra sfm --extra depth --extra open3d --extra research
```

## 3) Model downloads to the network volume

This project stores caches/weights under `"$CVP_VOLUME/models"`.

Recommended environment:

```bash
export CVP_VOLUME=/runpod-volume
export CVP_WORKDIR=/tmp/cv_pipeline_work
```

Download the default v1 depth model assets (used by the pipeline today):

```bash
uv run python cv-pipeline/scripts/download_models.py depth-anything-metric --encoder vitl --dataset hypersim
```

This will:

- clone `Depth-Anything-V2` into `"$CVP_VOLUME/models/vendor/depth-anything-v2"`
- download the selected checkpoint into `"$CVP_VOLUME/models/checkpoints/"`

Optional (for future experiments / fallbacks in `PROJECT-PLAN.md`):

```bash
# clone/update all third-party repos referenced by the plan
uv run python cv-pipeline/scripts/download_models.py vendor-all

# download additional checkpoints (big)
uv run python cv-pipeline/scripts/download_models.py metric3d --model vit_small
uv run python cv-pipeline/scripts/download_models.py unidepth --repo lpiccinelli/unidepth-v1-vitl14
uv run python cv-pipeline/scripts/download_models.py moge --repo Ruicheng/moge-2-vitl-normal
uv run python cv-pipeline/scripts/download_models.py dust3r --model vitl_512_dpt
uv run python cv-pipeline/scripts/download_models.py mast3r --with-retrieval
```

The script records everything in `"$CVP_VOLUME/models/manifest.json"` (vendor commits + downloaded files + notes).

Notes:

- `dust3r` / `mast3r` checkpoints are **non-commercial** (CC BY-NC-SA 4.0) and may have additional dataset license requirements; check each repo’s notices before use.

## 4) Run on the sample collection

Upload `sample-collection/data/downloads/` into your pod (any path), then:

```bash
export CVP_VOLUME=/runpod-volume
export CVP_WORKDIR=/tmp/cv_pipeline_work

uv run cv-pipeline eval-streeteasy \
  --dataset /path/to/streeteasy_examples_20.json \
  --downloads /path/to/downloads \
  --limit 3
```

Outputs land in `"$CVP_VOLUME/runs/<run_id>/"`.

## Sweeps + reports

Run a small built-in sweep:

```bash
uv run cv-pipeline sweep-streeteasy \
  --dataset /path/to/streeteasy_examples_20.json \
  --downloads /path/to/downloads \
  --limit 3
```

Summarize an eval JSON (and optionally apply a saved conformal calibrator):

```bash
uv run cv-pipeline report-eval --eval-json /path/to/eval.json
uv run cv-pipeline report-eval --eval-json /path/to/eval.json --calibration-json /path/to/calibration_conformal.json
```

### Useful “full plan” flags (examples)

Learned matching + TSDF + Monte Carlo:

```bash
uv run cv-pipeline run \
  --images /path/to/listing_images \
  --colmap \
  --sfm-matching lightglue \
  --depth-model metric3d-v2 \
  --fusion tsdf \
  --uncertainty montecarlo
```

Depth ensemble:

```bash
uv run cv-pipeline run \
  --images /path/to/listing_images \
  --colmap \
  --sfm-matching lightglue \
  --depth-model ensemble \
  --depth-ensemble "metric3d-v2,unidepth-v1,depth-anything-metric" \
  --fusion tsdf \
  --uncertainty montecarlo
```

Conservative multi-component aggregation:

```bash
uv run cv-pipeline run \
  --images /path/to/listing_images \
  --colmap \
  --multi-component sum
```

## Recommended pod sizing (to run the full research plan)

You can run models sequentially, so VRAM is driven by the “largest single model / step”.

- **Recommended (comfortable):** 1× `48GB` GPU (e.g., L40S 48GB / RTX A6000 48GB), `16 vCPU`, `64GB RAM`
- **Minimum (can run everything with more compromises):** 1× `24GB` GPU (e.g., RTX 4090 24GB), `8–16 vCPU`, `32–64GB RAM`
- **No-compromise:** 1× `80GB` GPU (A100 80GB), `32 vCPU`, `128GB RAM`

Storage:

- **Network volume (persistent):** `>=200GB` recommended (models + caches + runs), `>=100GB` minimum for small experiments
- **Container disk (ephemeral):** `>=50GB` (COLMAP databases/intermediates + temporary outputs)

Quick sanity check (prints GPU VRAM + COLMAP availability):

```bash
uv run python cv-pipeline/scripts/doctor.py
```

## VRAM profiling (before you decide on smaller GPUs)

Once you’ve downloaded the model assets onto `CVP_VOLUME`, you can **measure peak CUDA memory** for the heavy steps.

1) Download the “full plan” model set (large):

```bash
uv run python cv-pipeline/scripts/download_models.py all
```

2) Run the VRAM profiler suite (DepthAnything + DUSt3R + MASt3R):

```bash
uv run python cv-pipeline/scripts/profile_vram.py all --amp --n-images 4 --size 512
```

If you want the global alignment step included (more realistic for DUSt3R/MASt3R “reconstruction”):

```bash
uv run python cv-pipeline/scripts/profile_vram.py all --amp --dust3r-align --mast3r-align --n-images 4 --size 512
```

3) Save a JSON report:

```bash
uv run python cv-pipeline/scripts/profile_vram.py all --amp --dust3r-align --mast3r-align --print-json \\
  --json-out \"$CVP_VOLUME/runs/vram_profile.json\"
```

Interpretation:

- The script reports **peak VRAM reserved** and a **recommended minimum** (`peak + 2GiB buffer`).
- If your peaks are far below `24GB`, you can likely run on smaller GPUs by reducing `--size`, `--n-images`, and keeping `--batch-size 1`.

## Storage sizing (verify before allocating a big volume)

This repo includes a “no download” estimator for the **model weight** footprint:

```bash
python cv-pipeline/scripts/estimate_storage.py
```

On `2026-02-01`, the “full plan” weight set is about **13GB** (`total_known_human`), and the script suggests:

- **Models-only minimum**: ~`26GB` (weights + conservative cache overhead)
- **Practical minimum for experiments**: `100GB`
- **Comfortable headroom**: `200GB`

Rule of thumb:

- Models-only: often `~20–40GB` is enough (weights + caches + vendor repos)
- Models + growing dataset + lots of runs: `100–200GB` becomes reasonable
