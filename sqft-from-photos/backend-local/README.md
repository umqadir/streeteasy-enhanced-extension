# SleepEasy Local Backend

A clean local backend for the extension that runs the existing v2 pipelines directly.

## What it does

- Uses current v2 pipeline components (single-image and multiview) with no model swap.
- Exposes extension-friendly endpoints:
  - `GET /health`
  - `GET /backend/config`
  - `POST /backend/config`
  - `POST /estimate/single`
  - `POST /estimate/multi`
- Supports local-only operation (no cloud requirement).
- Supports device policy:
  - `auto` (default): `cuda -> mps -> cpu`
  - `mps`: Apple Metal only (errors if unavailable)
  - `cpu`: force CPU
- Supports analysis mode:
  - `auto` (default): multi-photo requests use `dust3r-scene`
  - `single-image`: multi-photo requests force `single-image`

## Run

From repo root:

```bash
uv run --project v2-pipeline python backend-local/local_backend.py --host 127.0.0.1 --port 8787 --device-policy auto
```

If `8787` is already in use, run on another local port (for example `8791`) and set the same URL in the extension Side Panel `Backend` settings.

## Quick test

Use local sample photos (no network fetch):

```bash
curl -s http://127.0.0.1:8787/health | jq

curl -s -X POST http://127.0.0.1:8787/estimate/single \
  -H 'Content-Type: application/json' \
  -d '{"imagePath":"/ABS/PATH/TO/photo_00.jpg"}' | jq

curl -s -X POST http://127.0.0.1:8787/estimate/multi \
  -H 'Content-Type: application/json' \
  -d '{"imagePaths":["/ABS/PATH/TO/photo_00.jpg","/ABS/PATH/TO/photo_01.jpg"]}' | jq
```

## Extension wiring

The extension service worker now defaults to this backend URL:

- `http://127.0.0.1:8787`

Side panel includes backend controls to set:

- backend URL
- device policy (`auto` / `mps` / `cpu`)
- analysis mode (`auto` / `single-image`)

## Local vs Remote deployment notes

- **Local default**: no infra cost; best for privacy and iteration.
- **Remote option**: only needed for centralized hosting or multi-user usage.
- **Measured local profile from this repo (Apple Silicon host, warm cache):**
  - `single` (`/estimate/single`): ~10-14s
  - `multi` (`/estimate/multi`, 2 photos): ~18-22s
  - resident memory after model load: roughly ~0.8-1.1 GB (can spike higher during loading/inference)
- **CPU-only (forced via device policy):**
  - `single`: ~14s
  - `multi` (2 photos): ~137s

## Chrome compatibility

- Works with Chrome extension MV3 service worker fetch.
- Manifest includes localhost host permissions for backend access.

## Health and Request Contract

`GET /health` now reports:

- `capabilities.cudaAvailable`
- `capabilities.mpsAvailable`
- `analysisMode`
- `recommendation.analysisMode`

`POST /estimate/multi` accepts optional:

- `multiviewMethod`: `dust3r-scene` or `single-image`
