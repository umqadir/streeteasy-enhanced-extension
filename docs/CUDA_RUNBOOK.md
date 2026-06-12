# CUDA Bring-Up and Validation Runbook

Step-by-step validation of the multi-photo DUSt3R path on an NVIDIA desktop. Written to be executable verbatim, including by automation agents.

Goal:
- bring up `selfhost/` on an NVIDIA machine
- verify the multi-photo DUSt3R path runs on CUDA (not single-image fallback)

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

8. If the CUDA check fails, collect diagnostics:

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
