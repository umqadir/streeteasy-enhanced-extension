#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _default_volume_root() -> Path:
    env = os.environ.get("CVP_VOLUME")
    if env:
        return Path(env)
    for candidate in ("/runpod-volume", "/workspace"):
        p = Path(candidate)
        if p.exists():
            return p
    return Path.home() / ".cache" / "cv_pipeline"


def _bytes_to_gib(n: int) -> float:
    return n / (1024**3)


def _fmt_gib(n: int) -> str:
    return f"{_bytes_to_gib(n):.2f} GiB"


def _require_cuda() -> Any:
    try:
        import torch
    except Exception as e:  # pragma: no cover
        raise SystemExit("Missing torch. In the pod: `cd cv-pipeline && uv sync --extra gpu`.") from e

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is not available. This script must be run on an NVIDIA GPU pod.")
    return torch


def _cuda_device_info(torch) -> dict[str, object]:
    props = torch.cuda.get_device_properties(0)
    return {
        "name": props.name,
        "total_vram_bytes": int(props.total_memory),
        "total_vram_gib": _bytes_to_gib(int(props.total_memory)),
        "capability": f"{props.major}.{props.minor}",
    }


def _cuda_clear(torch) -> None:
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()


@dataclass(frozen=True)
class MemSnapshot:
    allocated_bytes: int
    reserved_bytes: int
    peak_allocated_bytes: int
    peak_reserved_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "allocated_bytes": self.allocated_bytes,
            "reserved_bytes": self.reserved_bytes,
            "peak_allocated_bytes": self.peak_allocated_bytes,
            "peak_reserved_bytes": self.peak_reserved_bytes,
            "allocated_gib": _bytes_to_gib(self.allocated_bytes),
            "reserved_gib": _bytes_to_gib(self.reserved_bytes),
            "peak_allocated_gib": _bytes_to_gib(self.peak_allocated_bytes),
            "peak_reserved_gib": _bytes_to_gib(self.peak_reserved_bytes),
        }


def _snapshot(torch) -> MemSnapshot:
    torch.cuda.synchronize()
    return MemSnapshot(
        allocated_bytes=int(torch.cuda.memory_allocated()),
        reserved_bytes=int(torch.cuda.memory_reserved()),
        peak_allocated_bytes=int(torch.cuda.max_memory_allocated()),
        peak_reserved_bytes=int(torch.cuda.max_memory_reserved()),
    )


def _measure(torch, name: str, fn) -> dict[str, object]:
    _cuda_clear(torch)
    torch.cuda.reset_peak_memory_stats()
    before = _snapshot(torch)
    out = fn()
    torch.cuda.synchronize()
    after = _snapshot(torch)
    return {
        "name": name,
        "before": before.to_dict(),
        "after": after.to_dict(),
        "delta_allocated_gib": after.to_dict()["allocated_gib"] - before.to_dict()["allocated_gib"],
        "delta_reserved_gib": after.to_dict()["reserved_gib"] - before.to_dict()["reserved_gib"],
        "out": out,
    }


def _measure_value(torch, name: str, fn, summarize) -> tuple[dict[str, object], Any]:
    """
    Same as _measure, but returns the actual value (for internal chaining) while storing only a JSON-safe summary.
    """
    _cuda_clear(torch)
    torch.cuda.reset_peak_memory_stats()
    before = _snapshot(torch)
    value = fn()
    torch.cuda.synchronize()
    after = _snapshot(torch)
    meas = {
        "name": name,
        "before": before.to_dict(),
        "after": after.to_dict(),
        "delta_allocated_gib": after.to_dict()["allocated_gib"] - before.to_dict()["allocated_gib"],
        "delta_reserved_gib": after.to_dict()["reserved_gib"] - before.to_dict()["reserved_gib"],
        "out": summarize(value),
    }
    return meas, value


def _make_random_images(tmp: Path, *, n: int, hw: tuple[int, int], seed: int = 0) -> list[Path]:
    rng = np.random.default_rng(seed)
    h, w = hw
    paths: list[Path] = []
    for i in range(n):
        arr = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
        p = tmp / f"img_{i:02d}.png"
        Image.fromarray(arr, mode="RGB").save(p)
        paths.append(p)
    return paths


def _profile_depth_anything_metric(args: argparse.Namespace) -> dict[str, object]:
    torch = _require_cuda()
    volume = Path(args.volume_root)

    vendor_root = volume / "models" / "vendor" / "depth-anything-v2" / "metric_depth"
    ckpt = volume / "models" / "checkpoints" / f"depth_anything_v2_metric_{args.dataset}_{args.encoder}.pth"
    if not vendor_root.exists():
        raise SystemExit(f"Missing vendor repo at {vendor_root}. Run download_models.py vendor-all.")
    if not ckpt.exists():
        raise SystemExit(f"Missing checkpoint at {ckpt}. Run download_models.py depth-anything-metric ...")

    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))

    import cv2  # noqa: F401
    from depth_anything_v2.dpt import DepthAnythingV2  # type: ignore

    model_configs = {
        "vits": {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]},
        "vitb": {"encoder": "vitb", "features": 128, "out_channels": [96, 192, 384, 768]},
        "vitl": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]},
        "vitg": {"encoder": "vitg", "features": 384, "out_channels": [1536, 1536, 1536, 1536]},
    }

    def load_model():
        model = DepthAnythingV2(**{**model_configs[args.encoder], "max_depth": float(args.max_depth_m)})
        state = torch.load(ckpt, map_location="cpu")
        model.load_state_dict(state)
        model = model.to("cuda").eval()
        return model

    def summarize_model(m) -> dict[str, object]:
        params = sum(int(p.numel()) for p in m.parameters())
        return {"params_m": params / 1e6}

    load_res, model = _measure_value(torch, "depth_anything_metric:load", load_model, summarize_model)

    # Synthetic image (BGR uint8 expected by infer_image path)
    rng = np.random.default_rng(0)
    raw = rng.integers(0, 256, size=(args.h, args.w, 3), dtype=np.uint8)

    dtype = None
    if args.precision == "fp16":
        dtype = torch.float16
    elif args.precision == "bf16":
        dtype = torch.bfloat16

    def run_infer(iters: int) -> None:
        with torch.inference_mode():
            for _ in range(iters):
                if dtype is None:
                    _ = model.infer_image(raw, int(args.input_size))
                else:
                    with torch.autocast(device_type="cuda", dtype=dtype):
                        _ = model.infer_image(raw, int(args.input_size))
        return None

    warm, _ = _measure_value(
        torch,
        "depth_anything_metric:warmup",
        lambda: run_infer(int(args.warmup)),
        lambda _: {"iters": int(args.warmup)},
    )
    meas, _ = _measure_value(
        torch,
        "depth_anything_metric:inference",
        lambda: run_infer(int(args.iters)),
        lambda _: {"iters": int(args.iters)},
    )

    # Cleanup
    del model
    _cuda_clear(torch)

    return {
        "config": {
            "encoder": args.encoder,
            "dataset": args.dataset,
            "input_size": int(args.input_size),
            "max_depth_m": float(args.max_depth_m),
            "image_hw": [int(args.h), int(args.w)],
            "precision": args.precision,
        },
        "load": load_res,
        "warmup": warm,
        "inference": meas,
    }


def _dust3r_custom_inference(pairs, model, device: str, *, batch_size: int, use_amp: bool):
    # Re-implement dust3r.inference.inference with optional AMP.
    from dust3r.inference import check_if_same_size, loss_of_one_batch
    from dust3r.utils.device import collate_with_cat, to_cpu

    multiple_shapes = not (check_if_same_size(pairs))
    if multiple_shapes:
        batch_size = 1

    result = []
    for i in range(0, len(pairs), batch_size):
        res = loss_of_one_batch(
            collate_with_cat(pairs[i : i + batch_size]),
            model,
            None,
            device,
            use_amp=use_amp,
        )
        result.append(to_cpu(res))
    return collate_with_cat(result, lists=multiple_shapes)


def _profile_dust3r(args: argparse.Namespace) -> dict[str, object]:
    torch = _require_cuda()
    volume = Path(args.volume_root)

    vendor_root = volume / "models" / "vendor" / "dust3r"
    ckpt = volume / "models" / "checkpoints" / "dust3r" / args.checkpoint
    if not vendor_root.exists():
        raise SystemExit(f"Missing vendor repo at {vendor_root}. Run download_models.py dust3r or vendor-all.")
    if not ckpt.exists():
        raise SystemExit(f"Missing checkpoint at {ckpt}. Run download_models.py dust3r ...")

    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))

    from dust3r.inference import inference  # noqa: F401
    from dust3r.model import AsymmetricCroCo3DStereo
    from dust3r.utils.image import load_images
    from dust3r.image_pairs import make_pairs
    from dust3r.cloud_opt import GlobalAlignerMode, global_aligner

    def load_model():
        model = AsymmetricCroCo3DStereo.from_pretrained(str(ckpt)).to("cuda").eval()
        return model

    def summarize_model(m) -> dict[str, object]:
        params = sum(int(p.numel()) for p in m.parameters())
        return {"params_m": params / 1e6}

    load_res, model = _measure_value(torch, "dust3r:load", load_model, summarize_model)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = _make_random_images(tmp, n=int(args.n_images), hw=(int(args.img_h), int(args.img_w)), seed=0)
        images = load_images([str(p) for p in paths], size=int(args.size), verbose=False)
        pairs = make_pairs(images, scene_graph=args.scene_graph, prefilter=None, symmetrize=True)

        def run_inf():
            return _dust3r_custom_inference(
                pairs,
                model,
                "cuda",
                batch_size=int(args.batch_size),
                use_amp=bool(args.amp),
            )

        def summarize_out(out) -> dict[str, object]:
            return {"pairs": len(pairs), "out_keys": sorted(list(out.keys()))}

        inf_res, output = _measure_value(torch, "dust3r:inference", run_inf, summarize_out)

        align_res = None
        if args.align:
            def run_align():
                scene = global_aligner(output, device="cuda", mode=GlobalAlignerMode.PointCloudOptimizer)
                scene.compute_global_alignment(init="mst", niter=int(args.align_niter), schedule="cosine", lr=float(args.align_lr))
                return scene

            def summarize_scene(scene) -> dict[str, object]:
                return {"n_imgs": getattr(scene, "n_imgs", None)}

            align_res, _ = _measure_value(torch, "dust3r:global_alignment", run_align, summarize_scene)

    # Cleanup
    del model
    _cuda_clear(torch)

    return {
        "config": {
            "checkpoint": str(ckpt),
            "size": int(args.size),
            "n_images": int(args.n_images),
            "scene_graph": args.scene_graph,
            "batch_size": int(args.batch_size),
            "amp": bool(args.amp),
            "align": bool(args.align),
            "align_niter": int(args.align_niter),
        },
        "load": load_res,
        "inference": inf_res,
        "global_alignment": align_res,
    }


def _profile_mast3r(args: argparse.Namespace) -> dict[str, object]:
    torch = _require_cuda()
    volume = Path(args.volume_root)

    vendor_mast3r = volume / "models" / "vendor" / "mast3r"
    vendor_dust3r = volume / "models" / "vendor" / "dust3r"
    ckpt = volume / "models" / "checkpoints" / "mast3r" / args.checkpoint
    if not vendor_mast3r.exists():
        raise SystemExit(f"Missing vendor repo at {vendor_mast3r}. Run download_models.py mast3r or vendor-all.")
    if not vendor_dust3r.exists():
        raise SystemExit(f"Missing vendor repo at {vendor_dust3r}. MASt3R requires DUSt3R; run download_models.py dust3r.")
    if not ckpt.exists():
        raise SystemExit(f"Missing checkpoint at {ckpt}. Run download_models.py mast3r ...")

    # Ensure dust3r is importable first.
    for p in (vendor_dust3r, vendor_mast3r):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    import mast3r.utils.path_to_dust3r  # noqa: F401
    from mast3r.model import AsymmetricMASt3R
    from dust3r.utils.image import load_images
    from dust3r.image_pairs import make_pairs
    from dust3r.cloud_opt import GlobalAlignerMode, global_aligner

    def load_model():
        model = AsymmetricMASt3R.from_pretrained(str(ckpt)).to("cuda").eval()
        return model

    def summarize_model(m) -> dict[str, object]:
        params = sum(int(p.numel()) for p in m.parameters())
        return {"params_m": params / 1e6}

    load_res, model = _measure_value(torch, "mast3r:load", load_model, summarize_model)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = _make_random_images(tmp, n=int(args.n_images), hw=(int(args.img_h), int(args.img_w)), seed=1)
        images = load_images([str(p) for p in paths], size=int(args.size), verbose=False)
        pairs = make_pairs(images, scene_graph=args.scene_graph, prefilter=None, symmetrize=True)

        def run_inf():
            return _dust3r_custom_inference(
                pairs,
                model,
                "cuda",
                batch_size=int(args.batch_size),
                use_amp=bool(args.amp),
            )

        def summarize_out(out) -> dict[str, object]:
            return {"pairs": len(pairs), "out_keys": sorted(list(out.keys()))}

        inf_res, output = _measure_value(torch, "mast3r:inference", run_inf, summarize_out)

        align_res = None
        if args.align:
            def run_align():
                scene = global_aligner(output, device="cuda", mode=GlobalAlignerMode.PointCloudOptimizer)
                scene.compute_global_alignment(init="mst", niter=int(args.align_niter), schedule="cosine", lr=float(args.align_lr))
                return scene

            def summarize_scene(scene) -> dict[str, object]:
                return {"n_imgs": getattr(scene, "n_imgs", None)}

            align_res, _ = _measure_value(torch, "mast3r:global_alignment", run_align, summarize_scene)

    del model
    _cuda_clear(torch)

    return {
        "config": {
            "checkpoint": str(ckpt),
            "size": int(args.size),
            "n_images": int(args.n_images),
            "scene_graph": args.scene_graph,
            "batch_size": int(args.batch_size),
            "amp": bool(args.amp),
            "align": bool(args.align),
            "align_niter": int(args.align_niter),
        },
        "load": load_res,
        "inference": inf_res,
        "global_alignment": align_res,
    }


def _recommend_vram_gib(peak_reserved_gib: float) -> dict[str, object]:
    # Add a safety buffer for CUDA context, fragmentation, and extra tensors.
    buffered = peak_reserved_gib + 2.0
    return {
        "peak_reserved_gib": peak_reserved_gib,
        "recommended_min_gib": float(np.ceil(buffered)),
        "recommended_min_note": "recommended_min_gib = ceil(peak_reserved_gib + 2GiB buffer)",
    }


def _summarize(report: dict[str, object]) -> str:
    lines = []
    gpu = report.get("gpu", {})
    if isinstance(gpu, dict):
        lines.append(f"GPU: {gpu.get('name')} ({gpu.get('total_vram_gib'):.1f} GiB)")

    def pick_peak(obj: dict[str, object]) -> float | None:
        # Walk the result dict to find the max peak_reserved_gib.
        best = None
        if isinstance(obj, dict):
            if "after" in obj and isinstance(obj["after"], dict) and "peak_reserved_gib" in obj["after"]:
                v = float(obj["after"]["peak_reserved_gib"])  # type: ignore[arg-type]
                best = v if best is None else max(best, v)
            for v in obj.values():
                b = pick_peak(v) if isinstance(v, dict) else None
                if b is not None:
                    best = b if best is None else max(best, b)
        if isinstance(obj, list):
            for v in obj:
                b = pick_peak(v) if isinstance(v, dict) else None
                if b is not None:
                    best = b if best is None else max(best, b)
        return best

    peaks: list[tuple[str, float]] = []
    for k in ("depth_anything_metric", "dust3r", "mast3r"):
        if k in report:
            peak = pick_peak(report[k])  # type: ignore[arg-type]
            if peak is not None:
                peaks.append((k, peak))
    if peaks:
        lines.append("Peak VRAM (reserved):")
        for name, peak in peaks:
            rec = _recommend_vram_gib(peak)
            lines.append(f"- {name}: {peak:.2f} GiB (recommend >= {rec['recommended_min_gib']:.0f} GiB)")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Profile CUDA VRAM usage for research-plan models (run in GPU pod).")
    p.add_argument("--volume-root", type=Path, default=_default_volume_root())
    p.add_argument("--json-out", type=Path, default=None, help="Write full JSON report to this path.")
    p.add_argument("--print-json", action="store_true", help="Print full JSON report to stdout.")

    sub = p.add_subparsers(dest="cmd", required=True)

    da = sub.add_parser("depth-anything-metric", help="Profile Depth Anything V2 metric depth model.")
    da.add_argument("--encoder", choices=["vits", "vitb", "vitl"], default="vitl")
    da.add_argument("--dataset", choices=["hypersim", "vkitti"], default="hypersim")
    da.add_argument("--input-size", type=int, default=518)
    da.add_argument("--max-depth-m", type=float, default=20.0)
    da.add_argument("--h", type=int, default=768)
    da.add_argument("--w", type=int, default=1024)
    da.add_argument("--precision", choices=["fp32", "fp16", "bf16"], default="fp16")
    da.add_argument("--warmup", type=int, default=1)
    da.add_argument("--iters", type=int, default=3)
    da.set_defaults(func=_profile_depth_anything_metric)

    d3 = sub.add_parser("dust3r", help="Profile DUSt3R (inference + optional global alignment).")
    d3.add_argument("--checkpoint", default="DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth")
    d3.add_argument("--size", type=int, default=512, help="DUSt3R load_images size (typically 512).")
    d3.add_argument("--n-images", type=int, default=4)
    d3.add_argument("--scene-graph", default="complete", help="e.g. complete / star / chain (see dust3r make_pairs).")
    d3.add_argument("--batch-size", type=int, default=1)
    d3.add_argument("--amp", action="store_true", help="Use AMP for dust3r forward pass (reduces activations).")
    d3.add_argument("--align", action="store_true", help="Also run PointCloudOptimizer global alignment.")
    d3.add_argument("--align-niter", type=int, default=100)
    d3.add_argument("--align-lr", type=float, default=0.01)
    d3.add_argument("--img-h", type=int, default=768)
    d3.add_argument("--img-w", type=int, default=1024)
    d3.set_defaults(func=_profile_dust3r)

    ma = sub.add_parser("mast3r", help="Profile MASt3R (inference + optional global alignment).")
    ma.add_argument("--checkpoint", default="MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth")
    ma.add_argument("--size", type=int, default=512)
    ma.add_argument("--n-images", type=int, default=4)
    ma.add_argument("--scene-graph", default="complete")
    ma.add_argument("--batch-size", type=int, default=1)
    ma.add_argument("--amp", action="store_true")
    ma.add_argument("--align", action="store_true")
    ma.add_argument("--align-niter", type=int, default=100)
    ma.add_argument("--align-lr", type=float, default=0.01)
    ma.add_argument("--img-h", type=int, default=768)
    ma.add_argument("--img-w", type=int, default=1024)
    ma.set_defaults(func=_profile_mast3r)

    allp = sub.add_parser("all", help="Run a quick VRAM profile suite (DepthAnything + DUSt3R + MASt3R).")
    allp.add_argument("--dust3r-align", action="store_true")
    allp.add_argument("--mast3r-align", action="store_true")
    allp.add_argument("--n-images", type=int, default=4)
    allp.add_argument("--size", type=int, default=512)
    allp.add_argument("--amp", action="store_true")

    def _run_all(args: argparse.Namespace) -> dict[str, object]:
        torch = _require_cuda()
        report: dict[str, object] = {
            "gpu": _cuda_device_info(torch),
            "volume_root": str(args.volume_root),
        }
        report["depth_anything_metric"] = _profile_depth_anything_metric(
            argparse.Namespace(
                volume_root=args.volume_root,
                encoder="vitl",
                dataset="hypersim",
                input_size=518,
                max_depth_m=20.0,
                h=768,
                w=1024,
                precision="fp16",
                warmup=1,
                iters=2,
            )
        )
        report["dust3r"] = _profile_dust3r(
            argparse.Namespace(
                volume_root=args.volume_root,
                checkpoint="DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth",
                size=args.size,
                n_images=args.n_images,
                scene_graph="complete",
                batch_size=1,
                amp=args.amp,
                align=args.dust3r_align,
                align_niter=60,
                align_lr=0.01,
                img_h=768,
                img_w=1024,
            )
        )
        report["mast3r"] = _profile_mast3r(
            argparse.Namespace(
                volume_root=args.volume_root,
                checkpoint="MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth",
                size=args.size,
                n_images=args.n_images,
                scene_graph="complete",
                batch_size=1,
                amp=args.amp,
                align=args.mast3r_align,
                align_niter=60,
                align_lr=0.01,
                img_h=768,
                img_w=1024,
            )
        )
        return report

    allp.set_defaults(func=_run_all)

    args = p.parse_args()
    torch = _require_cuda()

    report: dict[str, object] = {
        "gpu": _cuda_device_info(torch),
        "volume_root": str(args.volume_root),
    }

    if args.cmd == "all":
        report = args.func(args)
    else:
        report[args.cmd.replace("-", "_")] = args.func(args)

    summary = _summarize(report)
    if summary:
        print(summary)

    if args.print_json:
        print(json.dumps(report, indent=2))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote JSON report to {args.json_out}")


if __name__ == "__main__":
    main()
