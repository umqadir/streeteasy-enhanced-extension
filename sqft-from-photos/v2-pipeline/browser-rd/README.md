# Browser Inference R&D (Commercial-Safe Path)

This folder probes whether a permissive MoGe-2 ONNX model can run in browser-like conditions.

## What this covers

1. Python ONNX Runtime probe (sanity + baseline latency/memory).
2. Headless Chrome probe using `onnxruntime-web` with:
   - `webgpu` (preferred)
   - `wasm` (fallback)

The probe uses the MoGe-2 ONNX checkpoint:
- `Ruicheng/moge-2-vits-normal-onnx` (smallest official ONNX variant)

## Quick run

```bash
cd /Users/uzairqadir/Projects/data-projects/streeteasy-enhanced-extension/sqft-from-photos
bash v2-pipeline/browser-rd/run_all.sh
```

Outputs are written under:

```text
v2-pipeline/runs/browser_rd_<timestamp>/
```

## Optional: summarize multiple model-size runs

```bash
uv run python v2-pipeline/browser-rd/summarize_model_scale.py \
  --vits-run /abs/path/to/browser_rd_<ts> \
  --vitb-run /abs/path/to/browser_rd_<ts>_vitb \
  --vitl-run /abs/path/to/browser_rd_<ts>_vitl \
  --out-md /abs/path/to/BROWSER_MODEL_SCALE_COMPARE.md
```

## Notes

- The ONNX model is loaded from Hugging Face and cached locally.
- Browser probe uses local static serving and Playwright-Core against local Chrome.
- If `webgpu` fails, the script still records the failure and continues with `wasm`.
