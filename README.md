# SleepEasy

This repository contains both:
- a canonical self-hosted distributable bundle
- retained research/experiment work

## Canonical Distribution Folder

Use `selfhost/` for install, run, and release packaging.

Quick start:

```bash
cd selfhost
bash scripts/install.sh
bash scripts/start_backend.sh
```

Then load unpacked extension from `selfhost/extension` in `chrome://extensions`.

Full instructions:
- `selfhost/README.md`

## Repository Layout

- `selfhost/`: canonical self-host bundle (extension + backend + v2 pipeline + scripts)
- `sqft-from-photos/`: research/experiments and data collection work
- `research/backend-archived/`: legacy backend code kept for reference
- `docs/`, `scripts/`: project utilities and reference docs

## Notes

- If you are shipping or testing the distributable, do not use ad-hoc paths from other folders; use `selfhost/` only.
- Local artifacts (`.playwright*`, `.artifacts`, runtime run folders) are intentionally ignored.

## Developer Utilities

- Compile extension data:
  - `node scripts/compile-data.js`
  - `uv run python scripts/compile-nta-exposure.py`
- Run local debug map:
  - `node scripts/serve-debug-map.js`
  - open `http://localhost:4173/docs/data-explorer.html`

## Agent Runbook: Desktop CUDA Bring-Up

If you are an automation agent and were only given this README, execute this runbook exactly.

Goal:
- bring up `selfhost/` on an NVIDIA desktop
- verify multi-photo DUSt3R path runs on CUDA (not single-image fallback)

1. Environment precheck:

```bash
nvidia-smi
uv --version
```

2. Install runtime:

```bash
cd selfhost
bash scripts/install.sh
```

3. Start backend (keep running in terminal A):

```bash
cd selfhost
bash scripts/start_backend.sh
```

4. Health + CUDA capability check (terminal B):

```bash
curl -sS http://127.0.0.1:8787/health | jq
```

Required:
- `.ok == true`
- `.capabilities.cudaAvailable == true`

5. Force runtime config:

```bash
curl -sS -X POST http://127.0.0.1:8787/backend/config \
  -H 'content-type: application/json' \
  -d '{"mode":"local","devicePolicy":"auto","analysisMode":"auto"}' | jq
```

6. CUDA multiview smoke test (real DUSt3R path):

```bash
curl -sS -X POST http://127.0.0.1:8787/estimate/multi \
  -H 'content-type: application/json' \
  -d '{
    "imageUrls": [
      "https://images.pexels.com/photos/271624/pexels-photo-271624.jpeg?auto=compress&cs=tinysrgb&w=800",
      "https://images.pexels.com/photos/1571460/pexels-photo-1571460.jpeg?auto=compress&cs=tinysrgb&w=800"
    ],
    "multiviewMethod": "dust3r-scene"
  }' | tee /tmp/sleepeasy_cuda_smoke.json | jq
```

Required:
- response has no `error` / `errorCode`
- `.pipeline == "multi"`
- `.request.multiviewMethod == "dust3r-scene"`

7. Extension wiring check:
- Open `chrome://extensions`
- Enable Developer Mode
- Load unpacked: `selfhost/extension`
- In sidepanel settings, set backend URL `http://127.0.0.1:8787`, check health
- Analyze a room with 2+ photos

Required:
- no no-CUDA prompt
- result pipeline is multi-view (`multi`)

8. If CUDA check fails, collect diagnostics:

```bash
nvidia-smi
cd selfhost
uv run --project backend python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
print("cuda_device_count", torch.cuda.device_count())
if torch.cuda.is_available():
    print("cuda_device_0", torch.cuda.get_device_name(0))
PY
```
