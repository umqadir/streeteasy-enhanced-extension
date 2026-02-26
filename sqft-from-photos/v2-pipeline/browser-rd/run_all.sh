#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${ROOT}/v2-pipeline/runs/browser_rd_${STAMP}"

mkdir -p "${OUT}"
mkdir -p "${OUT}/models"
mkdir -p "${OUT}/web-root/models"

echo "[1/4] Python ORT probe (MoGe ONNX)..."
uv run --with huggingface_hub --with onnx --with onnxruntime \
  python "${ROOT}/v2-pipeline/browser-rd/probe_moge_onnx.py" \
  --repo "Ruicheng/moge-2-vits-normal-onnx" \
  --height 320 \
  --width 320 \
  --num-tokens 1200 \
  --warmup 1 \
  --runs 3 \
  --out-dir "${OUT}"

cp -f "${OUT}/models/model.onnx" "${OUT}/web-root/models/model.onnx"
cp -f "${ROOT}/v2-pipeline/browser-rd/web/probe.html" "${OUT}/web-root/probe.html"

echo "[2/4] Browser probe (WebGPU)..."
node "${ROOT}/v2-pipeline/browser-rd/run_web_probe.mjs" \
  --root "${OUT}/web-root" \
  --model-path "/models/model.onnx" \
  --provider webgpu \
  --size 256 \
  --runs 2 \
  --warmup 1 \
  --num-tokens 1000 \
  --out-dir "${OUT}" \
  --port 8793 \
  --timeout-ms 300000 || true

echo "[3/4] Browser probe (WASM fallback)..."
node "${ROOT}/v2-pipeline/browser-rd/run_web_probe.mjs" \
  --root "${OUT}/web-root" \
  --model-path "/models/model.onnx" \
  --provider wasm \
  --size 256 \
  --runs 2 \
  --warmup 1 \
  --num-tokens 1000 \
  --out-dir "${OUT}" \
  --port 8794 \
  --timeout-ms 300000 || true

echo "[4/4] listing_001 COLMAP viability re-check (commercial-safe path)..."
export BROWSER_RD_OUT="${OUT}"
uv run --project "${ROOT}/v2-pipeline" python - <<'PY'
from pathlib import Path
import json
import os
import sys

ROOT = Path("/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/sqft-from-photos")
OUT = Path(os.environ["BROWSER_RD_OUT"])
images = ROOT / "sample-collection" / "clean_set_export" / "photos" / "listing_001"
sys.path.insert(0, str(ROOT / "cv-pipeline" / "src"))
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
from cv_pipeline.pipeline.runner import run_listing

configs = [
    ("colmap_exhaustive", "exhaustive"),
    ("colmap_lightglue", "lightglue"),
]
rows = []
for tag, matching in configs:
    out_json = OUT / f"{tag}.json"
    res = run_listing(
        images_dir=images,
        listing_id=f"listing_001_{tag}",
        label_sqft=266.0,
        image_selection=None,
        max_side=1536,
        use_colmap=True,
        sfm_matching=matching,
        pair_embed="torchvision-resnet50",
        pair_topk=10,
        pair_min_sim=0.2,
        multi_component="best",
        depth_model="moge-v2",
        depth_encoder="vitl",
        depth_dataset="hypersim",
        depth_input_size=518,
        depth_ensemble=None,
        max_depth_m=40.0,
        pc_stride=6,
        alpha=0.10,
        fusion="none",
        uncertainty="heuristic",
        mc_samples=100,
        fallback="depth-only",
        out_json=out_json,
    )
    rows.append({
        "tag": tag,
        "sqft_estimate": res.get("sqft_estimate"),
        "interval_90": res.get("sqft_interval_90"),
        "confidence": res.get("confidence_score"),
        "path": (res.get("diagnostics") or {}).get("path"),
        "colmap_error": (res.get("diagnostics") or {}).get("colmap_error"),
        "out_json": str(out_json),
    })

(OUT / "colmap_recheck_summary.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"ok": True, "rows": rows}, indent=2))
PY

uv run --project "${ROOT}/v2-pipeline" python - <<'PY'
from pathlib import Path
import json
import os

out = Path(os.environ["BROWSER_RD_OUT"])
py = json.loads((out / "python_ort_probe.json").read_text(encoding="utf-8"))
wg = json.loads((out / "web_probe_webgpu.json").read_text(encoding="utf-8"))
ws = json.loads((out / "web_probe_wasm.json").read_text(encoding="utf-8"))
col = json.loads((out / "colmap_recheck_summary.json").read_text(encoding="utf-8"))

lines = [
    "# listing_001 Browser + COLMAP Re-check",
    "",
    "## Browser (MoGe ONNX vits)",
    "",
    f"- model_size_mb: {py['model_size_mb']}",
    f"- python_ort_mean_ms: {py['latency_ms']['mean']:.1f}",
    f"- webgpu_mean_ms: {wg['probe']['timingMs']['latency']['mean']:.1f}",
    f"- wasm_mean_ms: {ws['probe']['timingMs']['latency']['mean']:.1f}",
    "",
    "## COLMAP viability",
    "",
    "| config | path | sqft_estimate | interval_90 | colmap_error_present |",
    "|---|---|---:|---:|---|",
]
for row in col:
    lo, hi = row.get("interval_90") or [None, None]
    lines.append(
        f"| {row.get('tag')} | {row.get('path')} | {float(row.get('sqft_estimate', 0.0)):.1f} | "
        f"[{float(lo):.1f}, {float(hi):.1f}] | {bool(row.get('colmap_error'))} |"
    )
lines += [
    "",
    "Artifacts:",
    f"- {out / 'python_ort_probe.json'}",
    f"- {out / 'web_probe_webgpu.json'}",
    f"- {out / 'web_probe_wasm.json'}",
    f"- {out / 'colmap_recheck_summary.json'}",
]
(out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

echo "Done: ${OUT}"
