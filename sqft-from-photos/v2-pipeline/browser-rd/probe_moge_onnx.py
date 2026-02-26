#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import resource
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _rss_mb() -> float:
    # macOS ru_maxrss is bytes.
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / (1024.0 * 1024.0)


def _download_model(repo: str, filename: str, local_dir: Path, hf_token: str | None) -> Path:
    from huggingface_hub import hf_hub_download

    local_dir.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(
        repo_id=repo,
        filename=filename,
        token=hf_token,
        local_dir=str(local_dir),
        local_dir_use_symlinks=False,
    )
    return Path(path).resolve()


def _build_feeds(session, h: int, w: int, num_tokens: int, seed: int) -> dict[str, object]:
    rng = np.random.default_rng(seed)
    image = rng.random((1, 3, h, w), dtype=np.float32)
    feeds: dict[str, object] = {}
    image_used = False
    token_used = False
    for inp in session.get_inputs():
        name = str(inp.name)
        lname = name.lower()
        if "token" in lname:
            # Accept either scalar or [1] token input.
            if inp.shape == []:
                feeds[name] = np.array(num_tokens, dtype=np.int64)
            else:
                feeds[name] = np.array([num_tokens], dtype=np.int64)
            token_used = True
        elif not image_used:
            feeds[name] = image
            image_used = True
        else:
            raise RuntimeError(f"Unexpected extra input '{name}', no feed strategy available.")
    if not image_used:
        raise RuntimeError("Model inputs did not include an image tensor.")
    if not token_used:
        # Some exports may bake token count.
        pass
    return feeds


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe ONNX runtime path (Python ORT).")
    parser.add_argument("--repo", default="Ruicheng/moge-2-vits-normal-onnx")
    parser.add_argument("--filename", default="model.onnx", help="HF repo file path (e.g. model.onnx or onnx/model.onnx)")
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--num-tokens", type=int, default=1200)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    out_dir = args.out_dir.expanduser().resolve()
    models_dir = out_dir / "models"
    out_dir.mkdir(parents=True, exist_ok=True)

    hf_token = os.getenv("HF_ACCESS_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    t_dl0 = time.time()
    model_path = _download_model(args.repo, args.filename, models_dir, hf_token)
    t_download = time.time() - t_dl0

    import onnx
    import onnxruntime as ort

    available = ort.get_available_providers()
    providers = [args.provider] if args.provider in available else ["CPUExecutionProvider"]
    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    t_load0 = time.time()
    session = ort.InferenceSession(str(model_path), providers=providers, sess_options=sess_opts)
    t_load = time.time() - t_load0

    feeds = _build_feeds(
        session=session,
        h=int(args.height),
        w=int(args.width),
        num_tokens=int(args.num_tokens),
        seed=int(args.seed),
    )

    for _ in range(max(0, int(args.warmup))):
        _ = session.run(None, feeds)

    latencies_ms: list[float] = []
    output_shapes: dict[str, list[int]] = {}
    output_dtypes: dict[str, str] = {}
    t_inf0 = time.time()
    for _ in range(max(1, int(args.runs))):
        t0 = time.perf_counter()
        out = session.run(None, feeds)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
    t_inf = time.time() - t_inf0

    for meta, arr in zip(session.get_outputs(), out, strict=False):
        np_arr = np.asarray(arr)
        output_shapes[str(meta.name)] = list(np_arr.shape)
        output_dtypes[str(meta.name)] = str(np_arr.dtype)

    onnx_model = onnx.load(str(model_path))
    result = {
        "timestamp_utc": _stamp(),
        "repo": str(args.repo),
        "filename": str(args.filename),
        "model_path": str(model_path),
        "model_size_mb": round(float(model_path.stat().st_size) / (1024.0 * 1024.0), 2),
        "onnx_ir_version": int(onnx_model.ir_version),
        "providers_requested": [args.provider],
        "providers_used": session.get_providers(),
        "available_providers": available,
        "input_shape": [1, 3, int(args.height), int(args.width)],
        "num_tokens": int(args.num_tokens),
        "warmup_runs": int(args.warmup),
        "timing_s": {
            "download": float(t_download),
            "session_load": float(t_load),
            "inference_total": float(t_inf),
        },
        "latency_ms": {
            "runs": [float(x) for x in latencies_ms],
            "mean": float(np.mean(latencies_ms)),
            "p50": float(np.percentile(latencies_ms, 50)),
            "p90": float(np.percentile(latencies_ms, 90)),
        },
        "output_shapes": output_shapes,
        "output_dtypes": output_dtypes,
        "rss_mb_max": float(_rss_mb()),
    }

    out_json = out_dir / "python_ort_probe.json"
    out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out_json": str(out_json)}, indent=2))


if __name__ == "__main__":
    main()
