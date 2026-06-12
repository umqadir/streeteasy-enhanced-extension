# SleepEasy Self-Host Bundle

The canonical release bundle: Chrome extension + local CV backend. If you only want the crime stats, you just need `extension/` — no backend, no Python. The backend powers the room square-footage feature.

## Requirements (backend only)

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (manages Python and dependencies automatically)
- ~4 GB disk for model weights (downloaded once to `~/.cache/cv_pipeline/models/`)
- Any OS. An NVIDIA GPU enables the more accurate multi-photo mode; CPU and Apple Silicon machines use single-image mode.

## Setup

1. Install the backend environment and download models (10–15 minutes on first run):

```bash
cd selfhost
bash scripts/install.sh
```

2. Start the backend (leave running):

```bash
bash scripts/start_backend.sh
```

3. Load the extension in Chrome:

- Open `chrome://extensions`
- Enable **Developer mode** (top right)
- Click **Load unpacked** and select `selfhost/extension`

4. Open a StreetEasy listing. Crime stats appear inline on the page; the SleepEasy side panel manages rooms and square-footage analysis.

## Analysis modes

- **Multi-photo (default)**: DUSt3R multi-view reconstruction — requires CUDA. ~5% error on the benchmark room.
- **Single-image**: SegFormer + MoGe-2 on each photo independently — runs anywhere. ~20% error, biased low (measures visible floor only).

On a machine without CUDA, the first analysis prompts once to switch to single-image mode; the choice persists and can be changed any time in side panel settings.

## Side panel settings

- Backend URL (default `http://127.0.0.1:8787`)
- Device policy (`auto`, `mps`, `cpu`)
- Analysis mode (`auto` = DUSt3R multi-view, or `single-image`)

## Scripts

- `scripts/install.sh` — installs the Python env and downloads DUSt3R + MoGe assets
- `scripts/start_backend.sh` — runs the local backend
- `scripts/doctor.sh` — environment and asset checks; run this first if anything misbehaves
- `scripts/bootstrap_models.py` — model/vendor download helper (called by install)
- `scripts/smoke_playwright_no_cuda.sh` — automated UX smoke test for the no-CUDA fallback path (requires `playwright-cli`; start the backend first)

## Troubleshooting

- **Backend won't start / import errors**: `bash scripts/doctor.sh` — it checks uv, Python 3.11, and model assets.
- **Extension can't reach backend**: confirm `curl http://127.0.0.1:8787/health` returns `"ok": true`, then re-check the backend URL in side panel settings.
- **Analysis is slow**: first request loads models into memory (1–3 minutes); subsequent requests are much faster. CPU-only machines are inherently slower than MPS/CUDA.
- **CUDA machine validation**: follow [docs/CUDA_RUNBOOK.md](../docs/CUDA_RUNBOOK.md) for an exact bring-up checklist.
