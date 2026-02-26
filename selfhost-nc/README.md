# SleepEasy Self-Host (Non-Commercial)

This directory is a clean self-hosted release bundle for the extension + local CV backend.

## Important Usage Notice

This bundle is intended for personal, non-commercial self-hosting.
Some third-party model/code dependencies used by the multi-view path are non-commercial.
See `THIRD_PARTY_NOTICES.md` before use.

## What This Release Does

- Multi-photo default: `dust3r-scene` (DUSt3R path)
- If CUDA is unavailable:
  - first analyze attempt prompts once to enable single-image mode
  - choice is persisted in extension settings
  - single-image mode can be toggled manually in side panel settings

## Quick Start

1. From repo root, refresh this bundle from source files:

```bash
uv run selfhost-nc/scripts/sync_release.py
```

2. Install backend env + models:

```bash
cd selfhost-nc
bash scripts/install.sh
```

3. Start local backend:

```bash
bash scripts/start_backend.sh
```

4. Load extension in Chrome:

- Open `chrome://extensions`
- Enable Developer mode
- Click **Load unpacked**
- Select `selfhost-nc/extension`

5. Open a StreetEasy listing and use the side panel.

## Playwright UX Smoke Test (No CUDA)

Run this to validate extension UX <-> backend wiring on this machine class:

```bash
cd selfhost-nc
bash scripts/start_backend.sh
```

In a second terminal:

```bash
cd selfhost-nc
bash scripts/smoke_playwright_no_cuda.sh
```

What it validates (real backend-only path):
- Sidepanel can connect to local backend (`GET_BACKEND_HEALTH`)
- No-CUDA prompt appears once in `analysisMode=auto`
- Decline path persists `noCudaPromptHandled=true`
- Manual `single-image` mode emits `multiviewMethod=single-image` for multi-photo rooms
- Room result is stored with `pipeline=single`

See implementation details in:
- `selfhost-nc/docs/playwright-cli-extension-testing.md`

## Backend Controls

In side panel settings:

- Backend URL (default `http://127.0.0.1:8787`)
- Device policy (`auto`, `mps`, `cpu`)
- Analysis mode:
  - `Auto (DUSt3R multi-view, CUDA required)`
  - `Single-image mode (no CUDA required)`

## Scripts

- `scripts/sync_release.py` - copies latest extension/backend/pipeline files from source-of-truth paths
- `scripts/install.sh` - installs Python env and downloads DUSt3R + MoGe assets
- `scripts/start_backend.sh` - runs local backend
- `scripts/smoke_playwright_no_cuda.sh` - Playwright CLI UX smoke test for no-CUDA fallback
- `scripts/doctor.sh` - environment and asset checks
- `scripts/bootstrap_models.py` - model/vendor download helper
