#!/usr/bin/env python3
"""
Commercial-viable evaluation runner for listing_001.

This runner intentionally avoids non-commercial components from the current stack:
- No SegFormer (NV license is non-commercial)
- No DUSt3R
- No LightGlue+SuperPoint

It evaluates:
1) Single-image path: OneFormer (MIT) + MoGe-2 (MIT) + per-image fusion
2) Multi-view path candidates:
   - MoGe pose with stock ORB matcher
   - MoGe pose with ORB+SIFT hybrid matcher (OpenCV features only)

The best multi-view candidate is selected and exported as multiview_best.json.

All model loading is forced offline to reuse already-downloaded local weights.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Force local cache usage only; fail fast instead of re-downloading large assets.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

THIS_DIR = Path(__file__).resolve().parent
V2_DIR = THIS_DIR.parent
REPO_ROOT = V2_DIR.parent
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

import estimate_v2b as v2b


DEFAULT_LISTING_DIRS = [
    REPO_ROOT / "sample-collection" / "clean_set_export" / "photos" / "listing_001",
    REPO_ROOT / "sample-collection" / "streeteasy_eval_dataset" / "photos" / "listing_001",
]
DEFAULT_IMAGE_NAMES = ["photo_00.jpg", "photo_01.jpg"]

ONEFORMER_MODEL_NAME = "shi-labs/oneformer_ade20k_swin_large"

_oneformer_bundle: tuple[Any, Any, str] | None = None


@dataclass
class RunOutput:
    name: str
    method: str
    sqft: float
    ci_lo: float
    ci_hi: float
    elapsed_s: float
    abs_error_sqft: float | None
    json_path: Path
    debug_dir: Path
    diagnostics: dict[str, Any]


def _run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def _to_builtin(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _to_builtin(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_builtin(v) for v in obj]
    if isinstance(obj, tuple):
        return [_to_builtin(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _find_listing_dir(listing_dir: Path | None) -> Path:
    if listing_dir is not None:
        p = listing_dir.expanduser().resolve()
        if not p.is_dir():
            raise FileNotFoundError(f"Listing directory not found: {p}")
        return p
    for p in DEFAULT_LISTING_DIRS:
        if p.is_dir():
            return p
    raise FileNotFoundError(
        "Could not find listing_001 in expected locations. Pass --listing-dir explicitly."
    )


def _select_images(listing_dir: Path, image_names: list[str], max_images: int) -> list[Path]:
    all_images = v2b._find_images(listing_dir)
    if not all_images:
        raise RuntimeError(f"No images found in {listing_dir}")

    picked: list[Path] = []
    if image_names:
        wanted = {x.strip() for x in image_names if x.strip()}
        by_name = {p.name: p for p in all_images}
        for name in image_names:
            p = by_name.get(name)
            if p is not None:
                picked.append(p)
        if len(picked) == len(wanted):
            return picked[:max_images]

    return all_images[:max_images]


def _load_oneformer_local() -> tuple[Any, Any, str]:
    global _oneformer_bundle
    if _oneformer_bundle is not None:
        return _oneformer_bundle

    import torch
    from transformers import OneFormerForUniversalSegmentation, OneFormerProcessor

    processor = OneFormerProcessor.from_pretrained(ONEFORMER_MODEL_NAME, local_files_only=True)
    model = OneFormerForUniversalSegmentation.from_pretrained(ONEFORMER_MODEL_NAME, local_files_only=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    _oneformer_bundle = (processor, model.to(device).eval(), device)
    return _oneformer_bundle


def _floor_ids_from_id2label(id2label: dict[Any, Any]) -> set[int]:
    floor_ids = {3, 28}
    for k, v in id2label.items():
        try:
            idx = int(k)
        except Exception:
            try:
                idx = int(float(k))
            except Exception:
                continue
        lbl = str(v).lower()
        if "floor" in lbl or "rug" in lbl or "carpet" in lbl:
            floor_ids.add(idx)
    return floor_ids


def _segment_floor_oneformer(image_path: Path) -> np.ndarray:
    import torch
    from PIL import Image

    processor, model, device = _load_oneformer_local()
    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    inputs = processor(images=image, task_inputs=["semantic"], return_tensors="pt")
    for k, v in list(inputs.items()):
        if hasattr(v, "to"):
            inputs[k] = v.to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    seg = processor.post_process_semantic_segmentation(outputs, target_sizes=[(h, w)])[0]
    seg_np = np.asarray(seg.detach().cpu().numpy(), dtype=np.int32)
    floor_ids = _floor_ids_from_id2label(getattr(model.config, "id2label", {}))
    return np.isin(seg_np, list(floor_ids))


def _match_images_orb_sift_hybrid(
    img0_gray: np.ndarray,
    img1_gray: np.ndarray,
    *,
    max_matches: int = 5000,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """
    Commercial-safe matcher using OpenCV local features only (ORB + SIFT).
    Returns (pts0, pts1, diag), matching estimate_v2b._match_images_orb contract.
    """
    import cv2

    pts0_blocks: list[np.ndarray] = []
    pts1_blocks: list[np.ndarray] = []
    diag: dict[str, object] = {
        "n_orb_kp0": 0,
        "n_orb_kp1": 0,
        "n_orb_matches": 0,
        "n_sift_kp0": 0,
        "n_sift_kp1": 0,
        "n_sift_matches": 0,
    }

    half = max(100, int(max_matches // 2))

    # ORB branch
    orb = cv2.ORB_create(nfeatures=12000, fastThreshold=7)
    k0_orb, d0_orb = orb.detectAndCompute(img0_gray, None)
    k1_orb, d1_orb = orb.detectAndCompute(img1_gray, None)
    diag["n_orb_kp0"] = int(len(k0_orb) if k0_orb else 0)
    diag["n_orb_kp1"] = int(len(k1_orb) if k1_orb else 0)
    if d0_orb is not None and d1_orb is not None and k0_orb and k1_orb:
        bf_orb = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        raw_orb = list(bf_orb.match(d0_orb, d1_orb))
        raw_orb.sort(key=lambda m: float(m.distance))
        keep_orb = raw_orb[:half]
        if keep_orb:
            p0 = np.asarray([k0_orb[m.queryIdx].pt for m in keep_orb], dtype=np.float32)
            p1 = np.asarray([k1_orb[m.trainIdx].pt for m in keep_orb], dtype=np.float32)
            pts0_blocks.append(p0)
            pts1_blocks.append(p1)
            diag["n_orb_matches"] = int(len(keep_orb))

    # SIFT branch (if available in this OpenCV build)
    if hasattr(cv2, "SIFT_create"):
        sift = cv2.SIFT_create(nfeatures=8000, contrastThreshold=0.01, edgeThreshold=20, sigma=1.2)
        k0_sift, d0_sift = sift.detectAndCompute(img0_gray, None)
        k1_sift, d1_sift = sift.detectAndCompute(img1_gray, None)
        diag["n_sift_kp0"] = int(len(k0_sift) if k0_sift else 0)
        diag["n_sift_kp1"] = int(len(k1_sift) if k1_sift else 0)
        if d0_sift is not None and d1_sift is not None and k0_sift and k1_sift:
            bf_sift = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
            knn = bf_sift.knnMatch(d0_sift, d1_sift, k=2)
            good: list[object] = []
            for m_n in knn:
                if len(m_n) < 2:
                    continue
                m, n = m_n
                if float(m.distance) < 0.78 * float(n.distance):
                    good.append(m)
            good.sort(key=lambda m: float(m.distance))
            keep_sift = good[:half]
            if keep_sift:
                p0 = np.asarray([k0_sift[m.queryIdx].pt for m in keep_sift], dtype=np.float32)
                p1 = np.asarray([k1_sift[m.trainIdx].pt for m in keep_sift], dtype=np.float32)
                pts0_blocks.append(p0)
                pts1_blocks.append(p1)
                diag["n_sift_matches"] = int(len(keep_sift))

    if not pts0_blocks:
        return (
            np.zeros((0, 2), dtype=np.float32),
            np.zeros((0, 2), dtype=np.float32),
            {**diag, "n_matches": 0},
        )

    pts0 = np.concatenate(pts0_blocks, axis=0)
    pts1 = np.concatenate(pts1_blocks, axis=0)

    # Drop near-duplicate correspondences after pixel rounding.
    key = np.concatenate(
        [
            np.round(pts0).astype(np.int32),
            np.round(pts1).astype(np.int32),
        ],
        axis=1,
    )
    _, uniq_idx = np.unique(key, axis=0, return_index=True)
    uniq_idx = np.sort(uniq_idx)
    pts0 = pts0[uniq_idx]
    pts1 = pts1[uniq_idx]

    if pts0.shape[0] > max_matches:
        pts0 = pts0[:max_matches]
        pts1 = pts1[:max_matches]

    return pts0, pts1, {**diag, "n_matches": int(pts0.shape[0])}


@contextmanager
def _patch_v2b(patches: dict[str, object]):
    old: dict[str, object] = {}
    try:
        for name, fn in patches.items():
            old[name] = getattr(v2b, name)
            setattr(v2b, name, fn)
        yield
    finally:
        for name, fn in old.items():
            setattr(v2b, name, fn)


def _result_payload(result: v2b.EstimateResult) -> dict[str, Any]:
    payload = {
        "sqft": float(result.sqft),
        "area_m2": float(result.area_m2),
        "ci_90_lo": float(result.ci_lo),
        "ci_90_hi": float(result.ci_hi),
        "method": str(result.method),
        "visible_area_m2": None if result.visible_area_m2 is None else float(result.visible_area_m2),
        "rect_upper_m2": None if result.completed_area_m2 is None else float(result.completed_area_m2),
        "n_images": int(result.n_images),
        "elapsed_s": float(result.elapsed_s),
        "per_image": [
            {
                "image": str(pr.image_path),
                "area_sqft": float(pr.area_sqft),
                "area_m2": float(pr.area_m2),
                "floor_mask_frac": float(pr.floor_mask_frac),
                "n_floor_points_3d": int(pr.n_floor_points_3d),
                "plane_residual_m": float(pr.plane_residual),
                "depth_median_m": float(pr.depth_median_m),
            }
            for pr in result.per_image
        ],
        "diagnostics": _to_builtin(result.diagnostics),
    }
    return payload


def _run_single(
    *,
    images: list[Path],
    debug_dir: Path,
    out_dir: Path,
    known_room_sqft: float | None,
) -> RunOutput:
    patches = {
        "load_segformer": (lambda: None),
        "segment_floor": _segment_floor_oneformer,
    }
    with _patch_v2b(patches):
        res = v2b.run_pipeline(
            images,
            interactive=False,
            debug_dir=debug_dir,
            impute_room_corners=False,
            multiview_method="single-image",
        )

    abs_error = None if known_room_sqft is None else abs(float(res.sqft) - float(known_room_sqft))
    payload = _result_payload(res)
    out_path = out_dir / "single_image_oneformer_moge.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return RunOutput(
        name="single_image_oneformer_moge",
        method=str(res.method),
        sqft=float(res.sqft),
        ci_lo=float(res.ci_lo),
        ci_hi=float(res.ci_hi),
        elapsed_s=float(res.elapsed_s),
        abs_error_sqft=abs_error,
        json_path=out_path,
        debug_dir=debug_dir,
        diagnostics=payload.get("diagnostics", {}),
    )


def _run_multiview_candidate(
    *,
    name: str,
    images: list[Path],
    debug_dir: Path,
    out_dir: Path,
    known_room_sqft: float | None,
    patch_matcher: bool,
    allow_scale: bool,
) -> RunOutput:
    patches: dict[str, object] = {
        "load_segformer": (lambda: None),
        "segment_floor": _segment_floor_oneformer,
    }
    if patch_matcher:
        patches["_match_images_orb"] = _match_images_orb_sift_hybrid

    with _patch_v2b(patches):
        res = v2b.run_pipeline(
            images,
            interactive=False,
            debug_dir=debug_dir,
            impute_room_corners=False,
            multiview_method="moge-pose",
            moge_pose_matcher="orb",
            moge_pose_allow_scale=bool(allow_scale),
        )

    abs_error = None if known_room_sqft is None else abs(float(res.sqft) - float(known_room_sqft))
    payload = _result_payload(res)
    out_path = out_dir / f"{name}.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return RunOutput(
        name=name,
        method=str(res.method),
        sqft=float(res.sqft),
        ci_lo=float(res.ci_lo),
        ci_hi=float(res.ci_hi),
        elapsed_s=float(res.elapsed_s),
        abs_error_sqft=abs_error,
        json_path=out_path,
        debug_dir=debug_dir,
        diagnostics=payload.get("diagnostics", {}),
    )


def _best_multiview(candidates: list[RunOutput], known_room_sqft: float | None) -> RunOutput:
    if not candidates:
        raise ValueError("No multiview candidates provided.")
    if known_room_sqft is not None:
        return sorted(
            candidates,
            key=lambda c: (float(c.abs_error_sqft if c.abs_error_sqft is not None else 1e18), float(c.elapsed_s)),
        )[0]
    return sorted(candidates, key=lambda c: (-float(c.sqft), float(c.elapsed_s)))[0]


def _write_summary(
    *,
    out_dir: Path,
    listing_dir: Path,
    images: list[Path],
    known_room_sqft: float | None,
    single: RunOutput,
    multiview_candidates: list[RunOutput],
    multiview_best: RunOutput,
) -> Path:
    lines: list[str] = []
    lines.append("# Commercial-Viable Listing_001 Evaluation")
    lines.append("")
    lines.append(f"- Timestamp (UTC): `{datetime.now(timezone.utc).isoformat()}`")
    lines.append(f"- Listing dir: `{listing_dir}`")
    lines.append(f"- Images: `{', '.join(p.name for p in images)}`")
    if known_room_sqft is not None:
        lines.append(f"- Reference room sqft: `{known_room_sqft:.1f}`")
    lines.append("")
    lines.append("## Spec Used")
    lines.append("")
    lines.append("- Segmentation: `shi-labs/oneformer_ade20k_swin_large` (MIT)")
    lines.append("- Depth/geometry: `Ruicheng/moge-2-vitl-normal` (MIT)")
    lines.append("- Single-image fusion: `v2b` per-image path (`--multiview-method single-image`)")
    lines.append("- Multiview pose: `moge-pose`")
    lines.append("- Multiview candidates:")
    lines.append("  - `ORB` (`allow_scale=false`)")
    lines.append("  - `ORB` (`allow_scale=true`)")
    lines.append("  - `ORB+SIFT hybrid` (`allow_scale=false`)")
    lines.append("  - `ORB+SIFT hybrid` (`allow_scale=true`)")
    lines.append("- Excluded from this spec: `SegFormer`, `DUSt3R`, `LightGlue+SuperPoint`")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Path | Sqft | 90% CI | Runtime (s) | Abs Error (sqft) | Method |")
    lines.append("|---|---:|---:|---:|---:|---|")

    def _fmt_err(v: float | None) -> str:
        return "-" if v is None else f"{v:.1f}"

    rows = [single, *multiview_candidates]
    for r in rows:
        lines.append(
            f"| {r.name} | {r.sqft:.1f} | [{r.ci_lo:.1f}, {r.ci_hi:.1f}] | {r.elapsed_s:.2f} | {_fmt_err(r.abs_error_sqft)} | `{r.method}` |"
        )

    lines.append("")
    lines.append(f"Best multiview candidate: `{multiview_best.name}`")
    lines.append(f"- JSON: `{multiview_best.json_path}`")
    lines.append(f"- Debug dir: `{multiview_best.debug_dir}`")

    mv = multiview_best.diagnostics.get("multiview", {})
    cand = mv.get("candidates", {})
    detail = cand.get("moge-pose", {})
    pose_diag = detail.get("pose_diagnostics", {})
    if pose_diag:
        lines.append("")
        lines.append("## Best Multiview Pose Diagnostics")
        lines.append("")
        lines.append(f"- matcher: `{pose_diag.get('matcher')}`")
        lines.append(f"- n_raw_matches: `{pose_diag.get('n_raw_matches')}`")
        lines.append(f"- n_3d_pairs: `{pose_diag.get('n_3d_pairs')}`")
        lines.append(f"- n_inliers: `{pose_diag.get('n_inliers')}`")
        lines.append(f"- rmse_m: `{pose_diag.get('rmse_m')}`")

    summary_path = out_dir / "SUMMARY.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run commercial-viable single/multiview evaluation on listing_001")
    parser.add_argument("--listing-dir", type=Path, default=None, help="Optional explicit listing directory.")
    parser.add_argument(
        "--image-names",
        nargs="*",
        default=DEFAULT_IMAGE_NAMES,
        help=f"Image names to evaluate (default: {' '.join(DEFAULT_IMAGE_NAMES)}).",
    )
    parser.add_argument("--max-images", type=int, default=2)
    parser.add_argument("--known-room-sqft", type=float, default=266.0)
    parser.add_argument("--out-root", type=Path, default=THIS_DIR / "runs")
    args = parser.parse_args()

    listing_dir = _find_listing_dir(args.listing_dir)
    images = _select_images(listing_dir, list(args.image_names), max(1, int(args.max_images)))
    if len(images) < 2:
        raise RuntimeError(f"Need at least 2 images for multiview. Found {len(images)} in {listing_dir}")

    if not v2b.MOGE_CKPT.exists():
        raise FileNotFoundError(
            f"MoGe checkpoint not found at {v2b.MOGE_CKPT}. This runner is offline-only and will not download."
        )
    # Validate OneFormer availability in local cache before starting long runs.
    _load_oneformer_local()

    run_dir = args.out_root.expanduser().resolve() / _run_stamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    single_debug = run_dir / "debug" / "single_image_oneformer_moge"
    single = _run_single(
        images=images,
        debug_dir=single_debug,
        out_dir=run_dir,
        known_room_sqft=args.known_room_sqft,
    )

    mv_orb = _run_multiview_candidate(
        name="multiview_moge_pose_orb",
        images=images,
        debug_dir=run_dir / "debug" / "multiview_moge_pose_orb",
        out_dir=run_dir,
        known_room_sqft=args.known_room_sqft,
        patch_matcher=False,
        allow_scale=False,
    )
    mv_orb_scale = _run_multiview_candidate(
        name="multiview_moge_pose_orb_allow_scale",
        images=images,
        debug_dir=run_dir / "debug" / "multiview_moge_pose_orb_allow_scale",
        out_dir=run_dir,
        known_room_sqft=args.known_room_sqft,
        patch_matcher=False,
        allow_scale=True,
    )
    mv_orb_sift = _run_multiview_candidate(
        name="multiview_moge_pose_orb_sift_hybrid",
        images=images,
        debug_dir=run_dir / "debug" / "multiview_moge_pose_orb_sift_hybrid",
        out_dir=run_dir,
        known_room_sqft=args.known_room_sqft,
        patch_matcher=True,
        allow_scale=False,
    )
    mv_orb_sift_scale = _run_multiview_candidate(
        name="multiview_moge_pose_orb_sift_hybrid_allow_scale",
        images=images,
        debug_dir=run_dir / "debug" / "multiview_moge_pose_orb_sift_hybrid_allow_scale",
        out_dir=run_dir,
        known_room_sqft=args.known_room_sqft,
        patch_matcher=True,
        allow_scale=True,
    )
    mv_candidates = [mv_orb, mv_orb_scale, mv_orb_sift, mv_orb_sift_scale]
    mv_best = _best_multiview(mv_candidates, known_room_sqft=args.known_room_sqft)

    # Copy best-candidate payload to a stable filename.
    best_payload = json.loads(mv_best.json_path.read_text(encoding="utf-8"))
    best_out = run_dir / "multiview_best.json"
    best_out.write_text(json.dumps(best_payload, indent=2) + "\n", encoding="utf-8")

    summary_path = _write_summary(
        out_dir=run_dir,
        listing_dir=listing_dir,
        images=images,
        known_room_sqft=args.known_room_sqft,
        single=single,
        multiview_candidates=mv_candidates,
        multiview_best=mv_best,
    )

    total_s = time.time() - t0
    print(f"Run dir: {run_dir}")
    print(f"Single path sqft: {single.sqft:.1f} ({single.name})")
    print(f"Best multiview sqft: {mv_best.sqft:.1f} ({mv_best.name})")
    print(f"Summary: {summary_path}")
    print(f"Elapsed: {total_s:.2f}s")


if __name__ == "__main__":
    main()
