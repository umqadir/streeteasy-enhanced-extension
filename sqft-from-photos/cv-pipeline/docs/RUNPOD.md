Moved to `../README.md` (RunPod runbook + all experiments).
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
