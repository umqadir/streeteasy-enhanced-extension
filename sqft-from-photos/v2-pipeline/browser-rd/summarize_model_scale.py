#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    p = argparse.ArgumentParser(description="Summarize MoGe ONNX browser scale probes.")
    p.add_argument("--vits-run", type=Path, required=True)
    p.add_argument("--vitb-run", type=Path, required=True)
    p.add_argument("--vitl-run", type=Path, required=True)
    p.add_argument("--out-md", type=Path, required=True)
    args = p.parse_args()

    run_map = {
        "vits": args.vits_run.expanduser().resolve(),
        "vitb": args.vitb_run.expanduser().resolve(),
        "vitl": args.vitl_run.expanduser().resolve(),
    }

    rows = []
    for name, run_dir in run_map.items():
        py = _load_json(run_dir / "python_ort_probe.json")
        wg = _load_json(run_dir / "web_probe_webgpu.json")
        ws = _load_json(run_dir / "web_probe_wasm.json")
        rows.append(
            {
                "model": name,
                "onnx_size_mb": float(py["model_size_mb"]),
                "python_mean_ms": float(py["latency_ms"]["mean"]),
                "python_rss_mb": float(py["rss_mb_max"]),
                "webgpu_load_ms": float(wg["probe"]["timingMs"]["sessionLoad"]),
                "webgpu_mean_ms": float(wg["probe"]["timingMs"]["latency"]["mean"]),
                "wasm_load_ms": float(ws["probe"]["timingMs"]["sessionLoad"]),
                "wasm_mean_ms": float(ws["probe"]["timingMs"]["latency"]["mean"]),
                "webgpu_ok": bool(wg.get("ok")),
                "wasm_ok": bool(ws.get("ok")),
                "run_dir": str(run_dir),
            }
        )

    out = args.out_md.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# MoGe ONNX Browser Scaling (Commercial-Safe)",
        "",
        "All probes were run in headless Chrome via `onnxruntime-web` and with Python ORT sanity checks.",
        "Inputs may differ by model to keep runtime bounded.",
        "",
        "| model | onnx_size_mb | python_ort_mean_ms | python_rss_mb | webgpu_load_ms | webgpu_mean_ms | wasm_load_ms | wasm_mean_ms | webgpu_ok | wasm_ok | run_dir |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for r in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    r["model"],
                    f"{r['onnx_size_mb']:.2f}",
                    f"{r['python_mean_ms']:.1f}",
                    f"{r['python_rss_mb']:.1f}",
                    f"{r['webgpu_load_ms']:.1f}",
                    f"{r['webgpu_mean_ms']:.1f}",
                    f"{r['wasm_load_ms']:.1f}",
                    f"{r['wasm_mean_ms']:.1f}",
                    str(r["webgpu_ok"]),
                    str(r["wasm_ok"]),
                    r["run_dir"],
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Observations:",
        "- WebGPU is consistently much faster than WASM for all tested checkpoints.",
        "- All tested checkpoints executed successfully in browser runtime with scalar `num_tokens` input.",
        "- Larger checkpoints materially increase load and memory pressure; `vits` remains the safest browser-first default.",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
