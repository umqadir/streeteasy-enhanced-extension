#!/usr/bin/env python3
"""
v2b: Room floor area estimation from room photos.

Pipeline: SegFormer (floor segmentation) + MoGe-2 (metric depth + intrinsics + normals)
  1. Segment floor pixels (SegFormer-B5 on ADE20K)
  2. Predict metric 3D points + normals (MoGe-2)
  3. Select floor points = segmentation mask AND upward-facing normals
  4. Fit floor plane (RANSAC), enforce planarity
  5. Project floor points onto plane, compute area (alpha shape)
  6. Optional multi-view fusion: DUSt3R camera alignment + MoGe floor-point fusion
  7. Optional room completion: Manhattan-aligned room-bound rectangle

Usage:
    uv run python estimate_v2b.py                     # interactive TUI
    uv run python estimate_v2b.py /path/to/photos     # batch mode
    uv run python estimate_v2b.py photos/ --json out.json
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import resource
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ── Paths ─────────────────────────────────────────────────────────────────
VOLUME_ROOT = Path.home() / ".cache" / "cv_pipeline"
VENDOR_DIR = VOLUME_ROOT / "models" / "vendor"
CHECKPOINTS_DIR = VOLUME_ROOT / "models" / "checkpoints"
MOGE_VENDOR = VENDOR_DIR / "moge"
MOGE_CKPT = CHECKPOINTS_DIR / "moge" / "Ruicheng__moge-2-vitl-normal" / "model.pt"
DUST3R_VENDOR = VENDOR_DIR / "dust3r"
DUST3R_CKPT = CHECKPOINTS_DIR / "dust3r" / "DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth"
LIGHTGLUE_VENDOR = VENDOR_DIR / "lightglue"

# HuggingFace token compatibility: user exports HF_ACCESS_TOKEN.
if "HF_TOKEN" not in os.environ and "HF_ACCESS_TOKEN" in os.environ:
    os.environ["HF_TOKEN"] = os.environ["HF_ACCESS_TOKEN"]
if "HUGGINGFACE_HUB_TOKEN" not in os.environ and "HF_ACCESS_TOKEN" in os.environ:
    os.environ["HUGGINGFACE_HUB_TOKEN"] = os.environ["HF_ACCESS_TOKEN"]

SQFT_PER_M2 = 10.763910416709722
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DUST3R_DEFAULT_ITERS = 120

# ADE20K class indices (0-indexed) for floor surfaces
ADE20K_FLOOR = 3   # "floor, flooring"
ADE20K_RUG = 28    # "rug, carpet, carpeting"


# ── Data classes ──────────────────────────────────────────────────────────
@dataclass
class Plane:
    normal: np.ndarray
    d: float
    def signed_distance(self, pts: np.ndarray) -> np.ndarray:
        return pts @ self.normal + self.d


@dataclass
class PerImageResult:
    image_path: str
    floor_mask_frac: float     # fraction of image pixels that are floor
    n_floor_points_3d: int     # floor points after segmentation + normal filter
    area_m2: float             # visible floor area from this image
    area_sqft: float
    plane_residual: float      # RMS distance of floor points to fitted plane (meters)
    depth_median_m: float      # median depth of floor pixels


@dataclass
class EstimateResult:
    sqft: float
    area_m2: float
    ci_lo: float
    ci_hi: float
    n_images: int
    per_image: list[PerImageResult]
    elapsed_s: float
    method: str = "per-image-fusion"
    visible_area_m2: float | None = None
    completed_area_m2: float | None = None
    diagnostics: dict = field(default_factory=dict)


@dataclass
class AreaDebug:
    pts2d: np.ndarray | None = None
    poly_coords: np.ndarray | None = None
    alpha: float | None = None
    n_inliers: int = 0


@dataclass
class LocalFloorGeometry:
    image_index: int
    plane: Plane
    poly_coords: np.ndarray
    valid_floor_mask: np.ndarray
    points3d: np.ndarray
    area_m2: float


@dataclass
class PairwiseStitch:
    i: int
    j: int
    n_matches: int
    n_floor_matches: int
    n_inliers: int
    rmse_m: float
    estimator: str = "orb_floor"
    transform_i_from_j: np.ndarray | None = None
    src_uv_j: np.ndarray | None = None
    dst_uv_i: np.ndarray | None = None
    inlier_mask: np.ndarray | None = None


@dataclass
class Dust3RRecon:
    depthmaps: list[np.ndarray]
    pts3d: list[np.ndarray]
    masks: list[np.ndarray]
    cam2world: np.ndarray
    intrinsics: np.ndarray


@dataclass
class PoseEstDiagnostics:
    matcher: str
    allow_scale: bool
    n_raw_matches: int
    n_3d_pairs: int
    n_inliers: int
    rmse_m: float
    scale: float | None = None
    ransac_thresh_m: float | None = None
    ransac_iters: int | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


# ── TUI ───────────────────────────────────────────────────────────────────
def _box(lines: list[str], width: int = 44) -> str:
    top = "\u250c" + "\u2500" * width + "\u2510"
    bot = "\u2514" + "\u2500" * width + "\u2518"
    rows = ["\u2502" + f"  {l}".ljust(width) + "\u2502" for l in lines]
    return "\n".join([top] + rows + [bot])


def _step(n: int, total: int, label: str):
    sys.stdout.write(f"\r  [{n}/{total}] {label}...")
    sys.stdout.flush()


def _step_done(n: int, total: int, label: str, elapsed: float):
    sys.stdout.write(f"\r  [{n}/{total}] {label}... done ({elapsed:.1f}s)\n")
    sys.stdout.flush()


def _find_images(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")
    )


def _interactive_select_folder() -> Path:
    print("\n  sqft-from-photos v2b (SegFormer + MoGe-2)")
    print("  " + "\u2500" * 38)
    while True:
        raw = input("\n  Enter folder path (or 'q' to quit): ").strip()
        if raw.lower() == "q":
            sys.exit(0)
        p = Path(raw).expanduser().resolve()
        if not p.is_dir():
            print(f"  Not a directory: {p}")
            continue
        imgs = _find_images(p)
        if not imgs:
            print(f"  No images found in {p}")
            continue
        return p


def _interactive_pick_images(images: list[Path]) -> list[Path]:
    print(f"\n  Found {len(images)} images:")
    for i, img in enumerate(images):
        print(f"    {i+1}. {img.name}")
    print("\n  Pick 3-4 photos of the SAME room (different angles).")
    resp = input("  Enter numbers (e.g. '1 3 5') or 'all': ").strip()
    if resp.lower() == "all":
        return images
    try:
        picks = [int(x) for x in resp.split()]
        selected = [images[i - 1] for i in picks if 1 <= i <= len(images)]
        if not selected:
            print("  No valid selections, using all.")
            return images
        return selected
    except ValueError:
        return images


def _display_result(result: EstimateResult):
    headline = "Estimated room area"
    if result.method == "per-image-fusion":
        headline = "Visible floor area"

    lines = [
        f"{headline}:  {result.sqft:.0f} sqft",
        f"90% CI:              [{result.ci_lo:.0f} \u2013 {result.ci_hi:.0f}] sqft",
        f"Images used:         {result.n_images}",
        f"Method:              {result.method}",
        f"Total time:          {result.elapsed_s:.1f}s",
        "",
    ]
    if result.visible_area_m2 is not None and result.completed_area_m2 is not None:
        vis_sqft = result.visible_area_m2 * SQFT_PER_M2
        hi_sqft = result.completed_area_m2 * SQFT_PER_M2
        lines.insert(1, f"Visible fused:       {vis_sqft:.0f} sqft")
        lines.insert(2, f"Rect upper bound:    {hi_sqft:.0f} sqft")

    for pr in result.per_image:
        name = Path(pr.image_path).name
        lines.append(f"  {name}: {pr.area_sqft:.0f} sqft  "
                      f"({pr.floor_mask_frac*100:.0f}% floor, "
                      f"residual={pr.plane_residual:.3f}m)")
    print("\n" + _box(lines, width=max(50, max(len(l) for l in lines) + 4)))


def _save_debug_artifacts(
    *,
    image_path: Path,
    floor_mask: np.ndarray,
    depth: np.ndarray,
    area_debug: AreaDebug | None,
    out_dir: Path,
) -> dict[str, str]:
    """
    Save visual evidence for debugging per image:
      - original frame
      - floor mask
      - floor overlay
      - depth colormap
      - projected floor points + alpha-shape polygon
    """
    import cv2
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(image_path).convert("RGB")
    img_np = np.array(img)
    h, w = floor_mask.shape[:2]
    if img_np.shape[:2] != (h, w):
        img_np = np.array(img.resize((w, h)))

    original_path = out_dir / "original.jpg"
    Image.fromarray(img_np).save(original_path, quality=95)

    mask_u8 = (floor_mask.astype(np.uint8) * 255)
    floor_mask_path = out_dir / "floor_mask.png"
    Image.fromarray(mask_u8).save(floor_mask_path)

    overlay = img_np.copy()
    overlay[floor_mask] = np.array([30, 220, 80], dtype=np.uint8)
    blended = (0.6 * img_np + 0.4 * overlay).astype(np.uint8)
    floor_overlay_path = out_dir / "floor_overlay.jpg"
    Image.fromarray(blended).save(floor_overlay_path, quality=95)

    valid = np.isfinite(depth) & (depth > 0)
    if valid.any():
        lo, hi = np.percentile(depth[valid], [2.0, 98.0])
        denom = max(1e-6, float(hi - lo))
        norm = np.clip((depth - lo) / denom, 0.0, 1.0)
        gray = (norm * 255).astype(np.uint8)
        depth_color = cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
        depth_color[~valid] = 0
    else:
        depth_color = np.zeros((h, w, 3), dtype=np.uint8)
    depth_vis_path = out_dir / "depth_turbo.png"
    cv2.imwrite(str(depth_vis_path), depth_color)

    proj_path = out_dir / "floor_projection.png"
    canvas = np.full((1024, 1024, 3), 255, dtype=np.uint8)
    if area_debug is not None and area_debug.pts2d is not None and area_debug.pts2d.size > 0:
        pts = area_debug.pts2d
        xmin, ymin = np.min(pts, axis=0)
        xmax, ymax = np.max(pts, axis=0)
        sx = 900.0 / max(1e-6, float(xmax - xmin))
        sy = 900.0 / max(1e-6, float(ymax - ymin))
        s = min(sx, sy)
        tx = 512.0 - 0.5 * s * float(xmin + xmax)
        ty = 512.0 + 0.5 * s * float(ymin + ymax)
        uv = np.empty_like(pts)
        uv[:, 0] = s * pts[:, 0] + tx
        uv[:, 1] = -s * pts[:, 1] + ty
        uv_i = np.round(uv).astype(np.int32)
        uv_i[:, 0] = np.clip(uv_i[:, 0], 0, 1023)
        uv_i[:, 1] = np.clip(uv_i[:, 1], 0, 1023)
        canvas[uv_i[:, 1], uv_i[:, 0]] = np.array([80, 80, 80], dtype=np.uint8)

        if area_debug.poly_coords is not None and area_debug.poly_coords.size > 0:
            poly = area_debug.poly_coords
            poly_uv = np.empty_like(poly)
            poly_uv[:, 0] = s * poly[:, 0] + tx
            poly_uv[:, 1] = -s * poly[:, 1] + ty
            poly_uv_i = np.round(poly_uv).astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(canvas, [poly_uv_i], isClosed=True, color=(30, 30, 220), thickness=2)
    cv2.imwrite(str(proj_path), canvas)

    return {
        "original": str(original_path),
        "floor_mask": str(floor_mask_path),
        "floor_overlay": str(floor_overlay_path),
        "depth_turbo": str(depth_vis_path),
        "floor_projection": str(proj_path),
    }


def _run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def _max_rss_mb() -> float:
    """
    Return process peak resident set size in MB.
    On macOS ru_maxrss is bytes; on Linux it's kilobytes.
    """
    rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss / (1024.0 * 1024.0)
    return rss / 1024.0


# ── 1. Floor segmentation (SegFormer-B5 ADE20K) ──────────────────────────
_segformer_model = None
_segformer_processor = None


def load_segformer():
    global _segformer_model, _segformer_processor
    if _segformer_model is not None:
        return

    import torch
    from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

    model_name = "nvidia/segformer-b5-finetuned-ade-640-640"
    _segformer_processor = SegformerImageProcessor.from_pretrained(model_name)
    _segformer_model = SegformerForSemanticSegmentation.from_pretrained(model_name)

    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    _segformer_model = _segformer_model.to(device).eval()


def segment_floor(image_path: Path) -> np.ndarray:
    """Returns binary floor mask (H, W) bool at original image resolution."""
    load_segformer()

    import torch
    import torch.nn.functional as F
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    inputs = _segformer_processor(images=image, return_tensors="pt")
    inputs = {k: v.to(_segformer_model.device) for k, v in inputs.items()}

    with torch.no_grad():
        logits = _segformer_model(**inputs).logits  # (1, 150, H', W')

    upsampled = F.interpolate(logits, size=(h, w), mode="bilinear", align_corners=False)
    seg_map = upsampled.argmax(dim=1).squeeze().cpu().numpy()

    floor_mask = (seg_map == ADE20K_FLOOR) | (seg_map == ADE20K_RUG)
    return floor_mask.astype(bool)


# ── 2. MoGe-2 metric depth + intrinsics + normals ────────────────────────
_moge_model = None
_moge_device = "cpu"


def load_moge():
    global _moge_model, _moge_device
    if _moge_model is not None:
        return

    if str(MOGE_VENDOR) not in sys.path:
        sys.path.insert(0, str(MOGE_VENDOR))

    import torch
    from moge.model.v2 import MoGeModel

    source = str(MOGE_CKPT) if MOGE_CKPT.exists() else "Ruicheng/moge-2-vitl-normal"
    _moge_device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    _moge_model = MoGeModel.from_pretrained(source).to(_moge_device).eval()


@dataclass
class MoGeResult:
    points: np.ndarray      # (H, W, 3) metric 3D points in camera space
    depth: np.ndarray       # (H, W) metric depth in meters
    normals: np.ndarray     # (H, W, 3) surface normals
    mask: np.ndarray        # (H, W) bool valid mask
    intrinsics: np.ndarray  # (3, 3) camera intrinsics


def infer_moge(image_path: Path) -> MoGeResult:
    load_moge()

    import cv2
    import torch

    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError(f"Failed to read image: {image_path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    img_t = torch.tensor(rgb / 255.0, dtype=torch.float32, device=_moge_device).permute(2, 0, 1)

    use_fp16 = _moge_device.startswith("cuda")
    with torch.inference_mode():
        out = _moge_model.infer(
            img_t, fov_x=None, apply_mask=True,
            resolution_level=9, use_fp16=use_fp16,
        )

    points = out["points"].detach().float().cpu().numpy()
    depth = out["depth"].detach().float().cpu().numpy()
    mask = out["mask"].detach().cpu().numpy().astype(bool)
    normals = out.get("normal")
    if normals is not None:
        normals = normals.detach().float().cpu().numpy()
    else:
        normals = np.zeros_like(points)

    intrinsics = out["intrinsics"].detach().float().cpu().numpy()

    # Squeeze batch dim if present
    if points.ndim == 4:
        points = points.squeeze(0)
    if depth.ndim == 3:
        depth = depth.squeeze(0)
    if mask.ndim == 3:
        mask = mask.squeeze(0)
    if normals.ndim == 4:
        normals = normals.squeeze(0)
    if intrinsics.ndim == 3:
        intrinsics = intrinsics.squeeze(0)

    return MoGeResult(points=points, depth=depth, normals=normals,
                      mask=mask, intrinsics=intrinsics)


# ── 2b. DUSt3R reconstruction + metric scale alignment ───────────────────
_dust3r_model = None
_dust3r_device = "cpu"


def load_dust3r():
    global _dust3r_model, _dust3r_device
    if _dust3r_model is not None:
        return

    if str(DUST3R_VENDOR) not in sys.path:
        sys.path.insert(0, str(DUST3R_VENDOR))

    import torch
    if hasattr(torch.serialization, "add_safe_globals"):
        torch.serialization.add_safe_globals([argparse.Namespace])
    from dust3r.model import AsymmetricCroCo3DStereo

    _dust3r_device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    _dust3r_model = AsymmetricCroCo3DStereo.from_pretrained(str(DUST3R_CKPT)).to(_dust3r_device).eval()


def infer_dust3r_scene(images: list[Path], *, niter: int = DUST3R_DEFAULT_ITERS) -> Dust3RRecon:
    load_dust3r()

    from dust3r.cloud_opt import GlobalAlignerMode, global_aligner
    from dust3r.image_pairs import make_pairs
    from dust3r.inference import inference
    from dust3r.utils.image import load_images

    imgs = load_images([str(p) for p in images], size=512)
    pairs = make_pairs(imgs, scene_graph="complete", prefilter=None, symmetrize=True)
    output = inference(pairs, _dust3r_model, _dust3r_device, batch_size=1, verbose=False)

    scene = global_aligner(output, device=_dust3r_device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init="mst", niter=int(niter), schedule="cosine", lr=0.01)

    depthmaps = [d.detach().float().cpu().numpy() for d in scene.get_depthmaps()]
    pts3d = [p.detach().float().cpu().numpy() for p in scene.get_pts3d()]
    masks = [m.detach().cpu().numpy().astype(bool) for m in scene.get_masks()]
    cam2world = scene.get_im_poses().detach().float().cpu().numpy()
    intrinsics = scene.get_intrinsics().detach().float().cpu().numpy()

    del scene, output, pairs, imgs
    gc.collect()

    return Dust3RRecon(
        depthmaps=depthmaps,
        pts3d=pts3d,
        masks=masks,
        cam2world=cam2world,
        intrinsics=intrinsics,
    )


def _robust_mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    return 1.4826 * mad


def estimate_scene_scale_m_per_unit(
    dust3r_depths: list[np.ndarray],
    metric_depths: list[np.ndarray],
    dust3r_masks: list[np.ndarray],
    *,
    max_depth_m: float = 25.0,
    min_pixels: int = 8_000,
) -> tuple[float, float]:
    """
    Robustly estimate metric scale for DUSt3R depth using per-image metric depths.
    Returns (scale_m_per_unit, relative_std).
    """
    import cv2

    per_image_scales: list[float] = []
    weights: list[float] = []
    for du, dm, m in zip(dust3r_depths, metric_depths, dust3r_masks):
        if dm.shape != du.shape:
            dm = cv2.resize(dm.astype(np.float32), (du.shape[1], du.shape[0]), interpolation=cv2.INTER_LINEAR)

        du = du.astype(np.float64)
        dm = dm.astype(np.float64)
        valid = np.isfinite(du) & np.isfinite(dm) & (du > 1e-6) & (dm > 1e-6) & (dm < max_depth_m) & m
        if int(valid.sum()) < min_pixels:
            continue

        ratios = dm[valid] / du[valid]
        med = float(np.median(ratios))
        if not np.isfinite(med) or med <= 0:
            continue

        mad = _robust_mad(ratios)
        if mad > 0:
            z = np.abs((ratios - med) / mad)
            inlier = z < 3.5
        else:
            inlier = np.ones_like(ratios, dtype=bool)
        if int(inlier.sum()) < min_pixels:
            continue

        s_i = float(np.median(ratios[inlier]))
        if np.isfinite(s_i) and s_i > 0:
            per_image_scales.append(s_i)
            weights.append(float(inlier.sum()))

    if not per_image_scales:
        raise RuntimeError("Failed to estimate DUSt3R metric scale from depth pairs.")

    values = np.asarray(per_image_scales, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    order = np.argsort(values)
    values = values[order]
    w = w[order]
    cum = np.cumsum(w)
    idx = int(np.searchsorted(cum, 0.5 * cum[-1]))
    scale = float(values[min(idx, len(values) - 1)])
    rel_std = float(_robust_mad(values) / max(scale, 1e-9))
    return scale, rel_std


# ── 2c. MoGe-only multi-view pose estimation (commercial-friendly) ───────

def _normalize_moge_intrinsics_to_pixels(k: np.ndarray, *, w: int, h: int) -> np.ndarray:
    """
    MoGe often returns "normalized" intrinsics (fx,fy,cx,cy in ~[0,1] units).
    Convert to pixel-space if values look normalized.
    """
    K = np.asarray(k, dtype=np.float64)
    if K.shape != (3, 3):
        return np.eye(3, dtype=np.float64)
    fx, fy, cx, cy = float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])
    # Heuristic: normalized intrinsics typically have fx~[0.5,2.0], cx~[0.4,0.6].
    if 0.0 < fx < 10.0 and 0.0 < fy < 10.0 and 0.0 < cx < 2.0 and 0.0 < cy < 2.0:
        fx *= float(w)
        fy *= float(h)
        cx *= float(w)
        cy *= float(h)
    out = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64)
    return out


def _match_images_orb(
    img0_gray: np.ndarray,
    img1_gray: np.ndarray,
    *,
    max_matches: int = 5000,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    import cv2

    orb = cv2.ORB_create(nfeatures=9000)
    k0, d0 = orb.detectAndCompute(img0_gray, None)
    k1, d1 = orb.detectAndCompute(img1_gray, None)
    if d0 is None or d1 is None or not k0 or not k1:
        return (
            np.zeros((0, 2), dtype=np.float32),
            np.zeros((0, 2), dtype=np.float32),
            {"n_kp0": int(len(k0) if k0 else 0), "n_kp1": int(len(k1) if k1 else 0), "n_matches": 0},
        )

    # Cross-check produces more matches than Lowe ratio in these wood-floor interiors.
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    good = list(bf.match(d0, d1))
    good.sort(key=lambda m: float(m.distance))
    good = good[: int(max_matches)]
    pts0 = np.asarray([k0[m.queryIdx].pt for m in good], dtype=np.float32)
    pts1 = np.asarray([k1[m.trainIdx].pt for m in good], dtype=np.float32)
    return pts0, pts1, {"n_kp0": int(len(k0)), "n_kp1": int(len(k1)), "n_matches": int(len(good))}


def _match_images_lightglue_superpoint(
    img0_path: Path,
    img1_path: Path,
    *,
    max_keypoints: int = 2048,
    filter_threshold: float = 0.1,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """
    Learned keypoints + matching (SuperPoint + LightGlue). Requires vendored LightGlue repo.
    Returns matched (x,y) coordinates in each image.
    """
    # LightGlue imports can trigger duplicate OpenMP runtime issues on macOS.
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    if str(LIGHTGLUE_VENDOR) not in sys.path:
        sys.path.insert(0, str(LIGHTGLUE_VENDOR))

    import torch
    from lightglue import LightGlue, SuperPoint
    from lightglue.utils import load_image, rbd

    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    extractor = SuperPoint(max_num_keypoints=int(max_keypoints)).eval().to(device)
    matcher = LightGlue(features="superpoint", filter_threshold=float(filter_threshold)).eval().to(device)

    im0 = load_image(img0_path).to(device)
    im1 = load_image(img1_path).to(device)
    feats0 = extractor.extract(im0, resize=None)
    feats1 = extractor.extract(im1, resize=None)
    out = matcher({"image0": feats0, "image1": feats1})

    feats0_nb = rbd(feats0)
    feats1_nb = rbd(feats1)
    out_nb = rbd(out)

    matches = out_nb.get("matches")
    if matches is None:
        return np.zeros((0, 2), dtype=np.float32), np.zeros((0, 2), dtype=np.float32), {
            "device": device,
            "n_kp0": 0,
            "n_kp1": 0,
            "n_matches": 0,
        }

    matches_np = np.asarray(matches.detach().cpu().numpy(), dtype=np.int32)
    if matches_np.ndim != 2 or matches_np.shape[1] != 2 or matches_np.shape[0] <= 0:
        return np.zeros((0, 2), dtype=np.float32), np.zeros((0, 2), dtype=np.float32), {
            "device": device,
            "n_kp0": int(feats0_nb["keypoints"].shape[0]),
            "n_kp1": int(feats1_nb["keypoints"].shape[0]),
            "n_matches": 0,
        }

    k0 = np.asarray(feats0_nb["keypoints"].detach().cpu().numpy(), dtype=np.float32)
    k1 = np.asarray(feats1_nb["keypoints"].detach().cpu().numpy(), dtype=np.float32)
    pts0 = k0[matches_np[:, 0]]
    pts1 = k1[matches_np[:, 1]]
    return pts0, pts1, {
        "device": device,
        "n_kp0": int(k0.shape[0]),
        "n_kp1": int(k1.shape[0]),
        "n_matches": int(matches_np.shape[0]),
    }


def _rigid_from_3d3d(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Solve dst ~= src @ R.T + t (row-vector convention), with det(R)=+1.
    Returns (R, t).
    """
    A = np.asarray(src, dtype=np.float64)
    B = np.asarray(dst, dtype=np.float64)
    if A.shape[0] < 3:
        raise ValueError("Need >=3 points for rigid transform")
    a0 = np.mean(A, axis=0)
    b0 = np.mean(B, axis=0)
    Ac = A - a0[None, :]
    Bc = B - b0[None, :]
    H = Ac.T @ Bc
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if float(np.linalg.det(R)) < 0.0:
        Vt[-1, :] *= -1.0
        R = Vt.T @ U.T
    t = b0 - a0 @ R.T
    return R, t


def _similarity_from_3d3d(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """
    Solve dst ~= s * src @ R.T + t (row-vector convention), s>0.
    Returns (s, R, t).
    """
    A = np.asarray(src, dtype=np.float64)
    B = np.asarray(dst, dtype=np.float64)
    if A.shape[0] < 3:
        raise ValueError("Need >=3 points for similarity transform")
    a0 = np.mean(A, axis=0)
    b0 = np.mean(B, axis=0)
    Ac = A - a0[None, :]
    Bc = B - b0[None, :]
    H = Ac.T @ Bc
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if float(np.linalg.det(R)) < 0.0:
        Vt[-1, :] *= -1.0
        R = Vt.T @ U.T
    denom = float(np.sum(Ac ** 2))
    if denom <= 1e-12:
        s = 1.0
    else:
        # Least squares scale after rotation: minimize ||s*(A@R.T) - B||.
        Ar = Ac @ R.T
        s = float(np.sum(Bc * Ar) / denom)
    if not np.isfinite(s) or s <= 0:
        s = 1.0
    t = b0 - (s * a0) @ R.T
    return s, R, t


def _ransac_3d3d(
    src: np.ndarray,
    dst: np.ndarray,
    *,
    allow_scale: bool,
    thresh_m: float,
    iters: int,
    rng: np.random.Generator,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Robustly fit dst ~= s*src@R.T + t (if allow_scale else s=1).
    Returns (s, R, t, inlier_mask, rmse_m).
    """
    A = np.asarray(src, dtype=np.float64)
    B = np.asarray(dst, dtype=np.float64)
    n = int(A.shape[0])
    if n < 6:
        raise RuntimeError("Too few 3D correspondences for RANSAC")

    best_inliers = None
    best_s = 1.0
    best_R = np.eye(3, dtype=np.float64)
    best_t = np.zeros((3,), dtype=np.float64)
    best_score = -1
    best_rmse = float("inf")

    for _ in range(int(iters)):
        idx = rng.choice(n, size=3, replace=False)
        try:
            if allow_scale:
                s, R, t = _similarity_from_3d3d(A[idx], B[idx])
            else:
                R, t = _rigid_from_3d3d(A[idx], B[idx])
                s = 1.0
        except Exception:
            continue
        pred = (s * (A @ R.T)) + t[None, :]
        err = np.linalg.norm(pred - B, axis=1)
        inliers = err < float(thresh_m)
        score = int(np.sum(inliers))
        if score < best_score:
            continue
        if score >= 6:
            rmse = float(np.sqrt(np.mean((err[inliers]) ** 2)))
        else:
            rmse = float("inf")
        if score > best_score or rmse < best_rmse:
            best_score = score
            best_rmse = rmse
            best_inliers = inliers
            best_s, best_R, best_t = float(s), np.asarray(R), np.asarray(t)

    if best_inliers is None or int(np.sum(best_inliers)) < 6:
        raise RuntimeError("RANSAC failed to find a valid pose")

    # Refine on inliers.
    if allow_scale:
        s, R, t = _similarity_from_3d3d(A[best_inliers], B[best_inliers])
    else:
        R, t = _rigid_from_3d3d(A[best_inliers], B[best_inliers])
        s = 1.0
    pred = (s * (A[best_inliers] @ R.T)) + t[None, :]
    rmse = float(np.sqrt(np.mean(np.sum((pred - B[best_inliers]) ** 2, axis=1))))
    return float(s), np.asarray(R, dtype=np.float64), np.asarray(t, dtype=np.float64), best_inliers, rmse


def infer_moge_pose_scene(
    images: list[Path],
    moge_results: list[MoGeResult],
    *,
    matcher: str = "orb",
    allow_scale: bool = False,
    ransac_thresh_m: float = 0.12,
    ransac_iters: int = 2200,
    prefilter_2d_fundamental: bool = True,
    fundamental_ransac_thresh_px: float = 3.0,
    use_pnp: bool = True,
    pnp_reproj_thresh_px: float = 8.0,
    pnp_iters: int = 2500,
    debug_dir: Path | None = None,
) -> tuple[Dust3RRecon, PoseEstDiagnostics]:
    """
    Estimate multi-view camera poses using only MoGe-2 pointmaps + image matching.
    Output is shaped like Dust3RRecon so downstream (reprojection/3D viewer) can be reused.
    """
    import cv2

    n = min(len(images), len(moge_results))
    if n <= 0:
        raise ValueError("No images for pose estimation")

    # Per-view geometry from MoGe (metric, camera-space).
    depthmaps: list[np.ndarray] = []
    pts3d: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    intrinsics: list[np.ndarray] = []
    for i in range(n):
        m = moge_results[i]
        depthmaps.append(np.asarray(m.depth, dtype=np.float32))
        pts3d.append(np.asarray(m.points, dtype=np.float32))
        masks.append(np.asarray(m.mask, dtype=bool))
        h, w = m.depth.shape[:2]
        intrinsics.append(_normalize_moge_intrinsics_to_pixels(m.intrinsics, w=w, h=h).astype(np.float32))

    cam2world = np.repeat(np.eye(4, dtype=np.float32)[None, :, :], n, axis=0)
    rng = np.random.default_rng(123)

    # Only pairwise (listing_001 has 2 images). Extendable to pose-graph later.
    if n >= 2:
        img0 = cv2.imread(str(images[0]), cv2.IMREAD_GRAYSCALE)
        img1 = cv2.imread(str(images[1]), cv2.IMREAD_GRAYSCALE)
        if img0 is None or img1 is None:
            raise RuntimeError("Failed to read images for matching")

        matcher_key = (matcher or "orb").strip().lower()
        if matcher_key == "orb":
            uv0, uv1, match_diag = _match_images_orb(img0, img1, max_matches=5000)
        elif matcher_key == "lightglue":
            uv0, uv1, match_diag = _match_images_lightglue_superpoint(images[0], images[1])
        else:
            raise ValueError(f"Unsupported matcher: {matcher}")
        n_raw = int(match_diag.get("n_matches", 0))

        if matcher_key == "orb" and prefilter_2d_fundamental and uv0.shape[0] >= 40:
            try:
                F, fmask = cv2.findFundamentalMat(
                    uv0.astype(np.float32),
                    uv1.astype(np.float32),
                    cv2.FM_RANSAC,
                    ransacReprojThreshold=float(fundamental_ransac_thresh_px),
                    confidence=0.999,
                )
                if fmask is not None:
                    keep = fmask.ravel().astype(bool)
                    # Only apply if we keep a healthy number of correspondences.
                    if int(np.sum(keep)) >= 80:
                        uv0 = uv0[keep]
                        uv1 = uv1[keep]
            except Exception:
                pass

        # Convert 2D matches to 3D-3D correspondences using MoGe pointmaps.
        p0 = moge_results[0].points
        p1 = moge_results[1].points
        m0 = np.asarray(moge_results[0].mask, dtype=bool)
        m1 = np.asarray(moge_results[1].mask, dtype=bool)
        h0, w0 = p0.shape[:2]
        h1, w1 = p1.shape[:2]
        px0 = np.clip(np.round(uv0[:, 0]).astype(np.int32), 0, w0 - 1)
        py0 = np.clip(np.round(uv0[:, 1]).astype(np.int32), 0, h0 - 1)
        px1 = np.clip(np.round(uv1[:, 0]).astype(np.int32), 0, w1 - 1)
        py1 = np.clip(np.round(uv1[:, 1]).astype(np.int32), 0, h1 - 1)

        pts0 = np.asarray(p0[py0, px0], dtype=np.float64)
        pts1 = np.asarray(p1[py1, px1], dtype=np.float64)
        valid = (
            m0[py0, px0]
            & m1[py1, px1]
            & np.isfinite(pts0).all(axis=1)
            & np.isfinite(pts1).all(axis=1)
        )
        z0 = pts0[:, 2]
        z1 = pts1[:, 2]
        valid = valid & (z0 > 0.10) & (z0 < 40.0) & (z1 > 0.10) & (z1 < 40.0)

        pts0 = pts0[valid]
        pts1 = pts1[valid]
        uv0v = uv0[valid]
        uv1v = uv1[valid]
        n_3d = int(pts0.shape[0])
        if n_3d < 40:
            raise RuntimeError(f"Too few 3D correspondences after filtering: {n_3d}")

        # Prefer 3D-2D PnP (more stable with many outliers) and only fall back to 3D-3D RANSAC.
        s = 1.0
        R_01 = None
        t_01 = None
        inliers = None
        rmse = float("inf")

        if use_pnp:
            try:
                # world = cam0. Estimate world->cam1 using 3D points from cam0 and 2D pixels in cam1.
                K1 = intrinsics[1].astype(np.float64)
                obj = pts0.astype(np.float64).reshape(-1, 1, 3)
                img = uv1v.astype(np.float64).reshape(-1, 1, 2)
                ok, rvec, tvec, pnp_inliers = cv2.solvePnPRansac(
                    obj,
                    img,
                    K1,
                    None,
                    iterationsCount=int(pnp_iters),
                    reprojectionError=float(pnp_reproj_thresh_px),
                    confidence=0.999,
                    flags=cv2.SOLVEPNP_EPNP,
                )
                if ok and pnp_inliers is not None and int(pnp_inliers.size) >= 12:
                    R_wc, _ = cv2.Rodrigues(rvec)
                    t_wc = np.asarray(tvec, dtype=np.float64).reshape(3)
                    # Convert world->cam1 (R_wc,t_wc) to cam1->world (row convention): R_cw=R_wc.T, t_cw=-R_wc.T@t_wc
                    R_cw = R_wc.T
                    t_cw = -R_cw @ t_wc
                    # This is cam1->world mapping (world=cam0), i.e., what cam2world[1] needs.
                    R_01 = R_cw
                    t_01 = t_cw
                    inliers = np.zeros((n_3d,), dtype=bool)
                    inliers[pnp_inliers.reshape(-1)] = True
                    # Compute 3D consistency RMSE using MoGe 3D points in cam1.
                    pred0 = pts1[inliers] @ R_01.T + t_01[None, :]
                    rmse = float(np.sqrt(np.mean(np.sum((pred0 - pts0[inliers]) ** 2, axis=1))))
            except Exception:
                pass

        if R_01 is None or t_01 is None or inliers is None or int(np.sum(inliers)) < 12:
            s, R_01, t_01, inliers, rmse = _ransac_3d3d(
                src=pts1,
                dst=pts0,
                allow_scale=bool(allow_scale),
                thresh_m=float(ransac_thresh_m),
                iters=int(ransac_iters),
                rng=rng,
            )

        # cam2world[0] is identity (world = view0). cam2world[1] maps cam1->world(view0).
        T = np.eye(4, dtype=np.float32)
        T[:3, :3] = np.asarray(R_01, dtype=np.float32)
        T[:3, 3] = np.asarray(t_01, dtype=np.float32)
        cam2world[1] = T

        artifacts: dict[str, str] = {}
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            try:
                draw = cv2.cvtColor(img0, cv2.COLOR_GRAY2BGR)
                draw1 = cv2.cvtColor(img1, cv2.COLOR_GRAY2BGR)
                panel = np.hstack([draw, draw1])
                w_off = draw.shape[1]
                # Draw a subset of inlier matches.
                idx_in = np.where(np.asarray(inliers, dtype=bool))[0]
                if idx_in.shape[0] > 600:
                    idx_in = rng.choice(idx_in, size=600, replace=False)
                for k in idx_in.tolist():
                    x0, y0 = float(uv0v[k, 0]), float(uv0v[k, 1])
                    x1, y1 = float(uv1v[k, 0]) + float(w_off), float(uv1v[k, 1])
                    cv2.line(panel, (int(x0), int(y0)), (int(x1), int(y1)), (40, 200, 40), 1, cv2.LINE_AA)
                out_path = debug_dir / "moge_pose_matches_inliers.png"
                cv2.imwrite(str(out_path), panel)
                artifacts["moge_pose_matches_inliers"] = str(out_path)
            except Exception:
                pass

        diag = PoseEstDiagnostics(
            matcher=str(matcher),
            allow_scale=bool(allow_scale),
            n_raw_matches=int(n_raw),
            n_3d_pairs=int(n_3d),
            n_inliers=int(np.sum(np.asarray(inliers, dtype=bool))),
            rmse_m=float(rmse),
            scale=None if not allow_scale else float(s),
            ransac_thresh_m=float(ransac_thresh_m),
            ransac_iters=int(ransac_iters),
            artifacts=artifacts,
        )
    else:
        diag = PoseEstDiagnostics(
            matcher=str(matcher),
            allow_scale=bool(allow_scale),
            n_raw_matches=0,
            n_3d_pairs=0,
            n_inliers=0,
            rmse_m=float("nan"),
        )

    recon = Dust3RRecon(
        depthmaps=depthmaps,
        pts3d=pts3d,
        masks=masks,
        cam2world=cam2world.astype(np.float32),
        intrinsics=np.stack(intrinsics, axis=0).astype(np.float32),
    )
    return recon, diag


# ── 3. Floor plane fitting + area computation ────────────────────────────

def _ransac_plane(points: np.ndarray, *, num_iter: int = 800,
                  distance_thresh: float = 0.04, rng: np.random.Generator) -> tuple[Plane, np.ndarray]:
    best_inliers = None
    best_plane = None
    n = points.shape[0]
    for _ in range(num_iter):
        idx = rng.choice(n, size=3, replace=False)
        p0, p1, p2 = points[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal = normal / norm
        d = -float(normal @ p0)
        plane = Plane(normal=normal, d=d)
        dist = np.abs(plane.signed_distance(points))
        inliers = dist < distance_thresh
        if best_inliers is None or inliers.sum() > best_inliers.sum():
            best_inliers = inliers
            best_plane = plane
    if best_plane is None:
        raise RuntimeError("RANSAC failed")
    return best_plane, best_inliers


def _plane_basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0.0, 0.0])
    if abs(float(a @ n)) > 0.9:
        a = np.array([0.0, 1.0, 0.0])
    u = np.cross(n, a); u /= np.linalg.norm(u)
    v = np.cross(n, u); v /= np.linalg.norm(v)
    return u, v


def _project_to_plane(points: np.ndarray, plane: Plane) -> np.ndarray:
    u, v = _plane_basis(plane.normal)
    p0 = -plane.d * plane.normal
    q = points - p0[None, :]
    return np.stack([q @ u, q @ v], axis=1)


def _alpha_shape(pts2d: np.ndarray, alpha: float):
    from scipy.spatial import Delaunay
    from shapely.geometry import LineString, MultiPoint, Polygon
    from shapely.ops import polygonize, unary_union

    if pts2d.shape[0] < 4:
        return MultiPoint(pts2d).convex_hull
    tri = Delaunay(pts2d)
    triangles = pts2d[tri.simplices]

    def circumradius(t):
        a, b, c = np.linalg.norm(t[0]-t[1]), np.linalg.norm(t[1]-t[2]), np.linalg.norm(t[2]-t[0])
        s = 0.5 * (a + b + c)
        area2 = max(s*(s-a)*(s-b)*(s-c), 0.0)
        return (a*b*c) / (4.0*np.sqrt(area2)) if area2 > 1e-18 else float("inf")

    edges: dict[tuple[int,int], int] = {}
    for simplex, t in zip(tri.simplices, triangles):
        r = circumradius(t)
        if r > alpha:
            continue
        for i, j in ((0,1),(1,2),(2,0)):
            edge = (min(simplex[i],simplex[j]), max(simplex[i],simplex[j]))
            edges[edge] = edges.get(edge, 0) + 1

    boundary = [e for e, count in edges.items() if count == 1]
    if not boundary:
        return MultiPoint(pts2d).convex_hull
    lines = [LineString([pts2d[i], pts2d[j]]) for i, j in boundary]
    polys = list(polygonize(lines))
    if not polys:
        return MultiPoint(pts2d).convex_hull
    merged = unary_union(polys)
    if isinstance(merged, Polygon):
        return merged
    return max(list(merged.geoms), key=lambda p: p.area)


def _auto_alpha(pts2d: np.ndarray) -> float:
    from scipy.spatial import cKDTree
    if pts2d.shape[0] < 10:
        return float("inf")
    tree = cKDTree(pts2d)
    d, _ = tree.query(pts2d, k=2)
    return 5.0 * float(np.median(d[:, 1]))


def compute_floor_area_single_image(
    floor_mask: np.ndarray,
    moge: MoGeResult,
    *,
    normal_thresh: float = 0.5,
    distance_thresh: float = 0.04,
    return_debug: bool = False,
) -> tuple[float, float, Plane | None, float, int, AreaDebug | None]:
    """
    Compute visible floor area from one image.

    Returns: (area_m2, area_sqft, plane, plane_residual_rms, n_floor_pts, area_debug)
    """
    rng = np.random.default_rng(42)

    # Combine segmentation mask with MoGe validity mask
    valid = floor_mask & moge.mask

    # Also filter by surface normal: floor normals should point roughly toward camera
    # In camera space, "up" is roughly -Y (camera coords: X right, Y down, Z forward)
    # Floor normals pointing "up" in world ≈ pointing in -Y direction in camera space
    # But MoGe normals are in camera space, and floor normals vary.
    # Use a softer criterion: normal Z component should be small (floor is ~horizontal)
    # and |normal Y component| should be large (pointing up or down in camera Y)
    if moge.normals is not None and moge.normals.any():
        ny = moge.normals[..., 1]  # Y component (camera coords: down is positive)
        # Floor normals point "up" in world = -Y in camera space → ny < 0
        # Accept normals where |ny| > threshold (strongly vertical)
        normal_filter = np.abs(ny) > normal_thresh
        valid = valid & normal_filter

    if valid.sum() < 100:
        return 0.0, 0.0, None, float("inf"), 0, None

    # Get 3D floor points
    floor_pts = moge.points[valid]

    # Subsample if too many points
    if floor_pts.shape[0] > 100_000:
        idx = rng.choice(floor_pts.shape[0], 100_000, replace=False)
        floor_pts = floor_pts[idx]

    # Fit plane
    try:
        plane, inliers = _ransac_plane(floor_pts, distance_thresh=distance_thresh, rng=rng)
    except RuntimeError:
        return 0.0, 0.0, None, float("inf"), 0, None

    # Planarity check: RMS residual
    dists = np.abs(plane.signed_distance(floor_pts[inliers]))
    residual_rms = float(np.sqrt(np.mean(dists ** 2)))

    # Project inlier floor points onto the plane
    inlier_pts = floor_pts[inliers]
    if inlier_pts.shape[0] < 10:
        return 0.0, 0.0, plane, residual_rms, int(inliers.sum()), None

    pts2d = _project_to_plane(inlier_pts, plane)
    alpha = _auto_alpha(pts2d)
    poly = _alpha_shape(pts2d, alpha)
    area_m2 = float(poly.area)
    debug_obj: AreaDebug | None = None
    if return_debug:
        poly_coords = None
        if hasattr(poly, "exterior") and poly.exterior is not None:
            poly_coords = np.asarray(poly.exterior.coords, dtype=np.float32)
        debug_obj = AreaDebug(
            pts2d=pts2d.astype(np.float32),
            poly_coords=poly_coords,
            alpha=float(alpha),
            n_inliers=int(inliers.sum()),
        )

    return area_m2, area_m2 * SQFT_PER_M2, plane, residual_rms, int(inliers.sum()), debug_obj


# ── 4. Multi-view stitching + room completion ────────────────────────────

def _fallback_fuse_floor_areas(per_image_results: list[PerImageResult]) -> tuple[float, float, float]:
    valid = [r for r in per_image_results if r.area_m2 > 0 and r.plane_residual < 0.10]
    if not valid:
        valid = [r for r in per_image_results if r.area_m2 > 0]
    if not valid:
        return 0.0, 0.0, 0.0

    areas = np.array([r.area_m2 for r in valid], dtype=np.float64)
    fused = float(np.percentile(areas, 75)) if len(areas) >= 3 else float(np.max(areas))
    lo = float(np.min(areas)) * 0.9
    hi = float(np.max(areas)) * 1.1
    return fused, lo, hi


def _estimate_gravity_up(cam2world: np.ndarray) -> np.ndarray:
    up_cam = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    ups = []
    for i in range(cam2world.shape[0]):
        R_c2w = cam2world[i, :3, :3]
        ups.append(R_c2w @ up_cam)
    v = np.mean(np.stack(ups), axis=0)
    n = np.linalg.norm(v)
    if n < 1e-9:
        return np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return v / n


def _find_floor_plane_in_scene(
    points: np.ndarray,
    cam_centers: np.ndarray,
    gravity_up: np.ndarray,
    *,
    distance_thresh: float = 0.06,
) -> tuple[Plane, np.ndarray, float]:
    rng = np.random.default_rng(42)
    if points.shape[0] > 220_000:
        idx = rng.choice(points.shape[0], size=220_000, replace=False)
        pts = points[idx]
    else:
        pts = points

    best_score = -1.0
    best_plane: Plane | None = None
    best_inliers: np.ndarray | None = None

    for _ in range(6):
        plane, inliers = _ransac_plane(pts, num_iter=600, distance_thresh=distance_thresh, rng=rng)
        n = plane.normal
        align = abs(float(n @ gravity_up))
        if float(n @ gravity_up) < 0:
            n = -n
            plane = Plane(normal=n, d=-plane.d)

        cam_dist = plane.signed_distance(cam_centers)
        frac_above = float((cam_dist > 0).mean())
        support = float(inliers.mean())
        score = support * (0.25 + 0.75 * align) * (0.25 + 0.75 * frac_above)

        if score > best_score:
            best_score = score
            best_plane = plane
            best_inliers = inliers

        pts = pts[~inliers]
        if pts.shape[0] < 12_000:
            break

    if best_plane is None or best_inliers is None:
        raise RuntimeError("Failed to find floor plane in reconstructed scene.")

    inliers_full = np.abs(best_plane.signed_distance(points)) < distance_thresh
    return best_plane, inliers_full, float(best_score)


def _safe_inv3x3(h: np.ndarray) -> np.ndarray | None:
    det = float(np.linalg.det(h[:2, :2]))
    if abs(det) < 1e-8:
        return None
    try:
        return np.linalg.inv(h)
    except np.linalg.LinAlgError:
        return None


def _affine_apply(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    x = pts[:, 0]
    y = pts[:, 1]
    out_x = h[0, 0] * x + h[0, 1] * y + h[0, 2]
    out_y = h[1, 0] * x + h[1, 1] * y + h[1, 2]
    return np.stack([out_x, out_y], axis=1)


def _fit_local_affine_with_ransac(
    src: np.ndarray,
    dst: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray | None, float]:
    import cv2

    if src.shape[0] < 6 or dst.shape[0] < 6:
        return None, None, float("inf")

    candidates: list[dict[str, object]] = []
    for mode, thr, partial_bonus in (
        ("partial", 0.10, 1),
        ("partial", 0.16, 1),
        ("full", 0.16, 0),
        ("full", 0.20, 0),
    ):
        if mode == "partial":
            a, inlier_mask = cv2.estimateAffinePartial2D(
                src,
                dst,
                method=cv2.RANSAC,
                ransacReprojThreshold=thr,
                maxIters=7000,
                confidence=0.995,
            )
        else:
            a, inlier_mask = cv2.estimateAffine2D(
                src,
                dst,
                method=cv2.RANSAC,
                ransacReprojThreshold=thr,
                maxIters=7000,
                confidence=0.995,
            )
        if a is None or inlier_mask is None:
            continue

        inliers = inlier_mask.reshape(-1).astype(bool)
        nin = int(inliers.sum())
        if nin < 6:
            continue

        h = np.eye(3, dtype=np.float64)
        h[:2, :] = a.astype(np.float64)
        lin = h[:2, :2]
        det = float(np.linalg.det(lin))
        if not np.isfinite(det) or det <= 0.0:
            continue
        svals = np.linalg.svd(lin, compute_uv=False)
        if svals[1] < 1e-6:
            continue
        anisotropy = float(svals[0] / svals[1])
        if anisotropy > 5.0:
            continue

        pred = _affine_apply(h, src.astype(np.float64))
        rmse = float(np.sqrt(np.mean(np.sum((pred[inliers] - dst[inliers]) ** 2, axis=1))))
        candidates.append(
            {
                "nin": int(nin),
                "neg_rmse": float(-rmse),
                "partial_bonus": int(partial_bonus),
                "h": h,
                "inliers": inliers.copy(),
            }
        )

    if not candidates:
        return None, None, float("inf")
    candidates.sort(
        key=lambda x: (int(x["nin"]), float(x["neg_rmse"]), int(x["partial_bonus"])),
        reverse=True,
    )
    best = candidates[0]
    return (
        np.asarray(best["h"], dtype=np.float64),
        np.asarray(best["inliers"], dtype=bool),
        float(-float(best["neg_rmse"])),
    )


def _project_pixel_to_local_floor(
    *,
    x: float,
    y: float,
    geom: LocalFloorGeometry,
) -> np.ndarray | None:
    h, w = geom.valid_floor_mask.shape[:2]
    xi = int(round(x))
    yi = int(round(y))
    if xi < 0 or xi >= w or yi < 0 or yi >= h:
        return None
    if not geom.valid_floor_mask[yi, xi]:
        return None
    p3 = geom.points3d[yi, xi]
    if not np.all(np.isfinite(p3)):
        return None
    return _project_to_plane(p3.reshape(1, 3), geom.plane)[0]


def _estimate_pairwise_stitch(
    *,
    i: int,
    j: int,
    img_i_gray: np.ndarray,
    img_j_gray: np.ndarray,
    geom_i: LocalFloorGeometry,
    geom_j: LocalFloorGeometry,
) -> PairwiseStitch:
    import cv2

    orb = cv2.ORB_create(nfeatures=5000, fastThreshold=7)
    kp_i, des_i = orb.detectAndCompute(img_i_gray, None)
    kp_j, des_j = orb.detectAndCompute(img_j_gray, None)
    if des_i is None or des_j is None or not kp_i or not kp_j:
        return PairwiseStitch(i=i, j=j, n_matches=0, n_floor_matches=0, n_inliers=0, rmse_m=float("inf"))

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    raw = bf.match(des_j, des_i)  # query: j, train: i
    if not raw:
        return PairwiseStitch(i=i, j=j, n_matches=0, n_floor_matches=0, n_inliers=0, rmse_m=float("inf"))

    raw = sorted(raw, key=lambda m: m.distance)
    dists = np.array([m.distance for m in raw], dtype=np.float32)
    dist_cut = max(42.0, float(np.median(dists) * 1.8))
    kept = [m for m in raw[:1200] if m.distance <= dist_cut]
    if len(kept) < 8:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(raw),
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
        )

    src_local: list[np.ndarray] = []
    dst_local: list[np.ndarray] = []
    src_uv_j: list[tuple[float, float]] = []
    dst_uv_i: list[tuple[float, float]] = []
    for m in kept:
        uv_j = kp_j[m.queryIdx].pt
        pj = _project_pixel_to_local_floor(
            x=uv_j[0],
            y=uv_j[1],
            geom=geom_j,
        )
        if pj is None:
            continue
        uv_i = kp_i[m.trainIdx].pt
        pi = _project_pixel_to_local_floor(
            x=uv_i[0],
            y=uv_i[1],
            geom=geom_i,
        )
        if pi is None:
            continue
        src_local.append(pj)
        dst_local.append(pi)
        src_uv_j.append((float(uv_j[0]), float(uv_j[1])))
        dst_uv_i.append((float(uv_i[0]), float(uv_i[1])))

    if len(src_local) < 8:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(raw),
            n_floor_matches=len(src_local),
            n_inliers=0,
            rmse_m=float("inf"),
        )

    src = np.asarray(src_local, dtype=np.float32)
    dst = np.asarray(dst_local, dtype=np.float32)
    src_uv_arr = np.asarray(src_uv_j, dtype=np.float32)
    dst_uv_arr = np.asarray(dst_uv_i, dtype=np.float32)
    best_h, best_inliers, best_rmse = _fit_local_affine_with_ransac(src, dst)
    if best_h is None or best_inliers is None:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(raw),
            n_floor_matches=len(src_local),
            n_inliers=0,
            rmse_m=float("inf"),
        )
    best_nin = int(best_inliers.sum())
    return PairwiseStitch(
        i=i,
        j=j,
        n_matches=len(raw),
        n_floor_matches=len(src_local),
        n_inliers=int(best_nin),
        rmse_m=float(best_rmse),
        estimator="orb_floor",
        transform_i_from_j=best_h,
        src_uv_j=src_uv_arr,
        dst_uv_i=dst_uv_arr,
        inlier_mask=best_inliers,
    )


def _estimate_pairwise_stitch_dense_flow(
    *,
    i: int,
    j: int,
    img_i_gray: np.ndarray,
    img_j_gray: np.ndarray,
    geom_i: LocalFloorGeometry,
    geom_j: LocalFloorGeometry,
) -> PairwiseStitch:
    import cv2

    if img_i_gray.shape[:2] != img_j_gray.shape[:2]:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=0,
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="dense_flow",
        )

    flow = cv2.calcOpticalFlowFarneback(
        img_j_gray,
        img_i_gray,
        None,
        pyr_scale=0.5,
        levels=4,
        winsize=31,
        iterations=5,
        poly_n=7,
        poly_sigma=1.5,
        flags=0,
    )
    h, w = img_j_gray.shape[:2]
    floor_j = np.asarray(geom_j.valid_floor_mask, dtype=bool)
    ys, xs = np.where(floor_j)
    if len(xs) < 200:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=0,
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="dense_flow",
        )

    # Coarse spatial subsampling so correspondences are spread across floor.
    stride = max(3, int(np.sqrt(max(1, len(xs) // 2200))))
    xs = xs[::stride]
    ys = ys[::stride]
    src_local: list[np.ndarray] = []
    dst_local: list[np.ndarray] = []
    src_uv_j: list[tuple[float, float]] = []
    dst_uv_i: list[tuple[float, float]] = []

    for xj_i, yj_i in zip(xs, ys):
        dx, dy = flow[yj_i, xj_i]
        xi = float(xj_i + dx)
        yi = float(yj_i + dy)
        if xi < 0 or xi >= (w - 1) or yi < 0 or yi >= (h - 1):
            continue
        pj = _project_pixel_to_local_floor(x=float(xj_i), y=float(yj_i), geom=geom_j)
        if pj is None:
            continue
        pi = _project_pixel_to_local_floor(x=xi, y=yi, geom=geom_i)
        if pi is None:
            continue
        src_local.append(pj)
        dst_local.append(pi)
        src_uv_j.append((float(xj_i), float(yj_i)))
        dst_uv_i.append((float(xi), float(yi)))

    if len(src_local) < 10:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=int(len(xs)),
            n_floor_matches=len(src_local),
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="dense_flow",
        )

    src = np.asarray(src_local, dtype=np.float32)
    dst = np.asarray(dst_local, dtype=np.float32)
    src_uv_arr = np.asarray(src_uv_j, dtype=np.float32)
    dst_uv_arr = np.asarray(dst_uv_i, dtype=np.float32)
    best_h, best_inliers, best_rmse = _fit_local_affine_with_ransac(src, dst)
    if best_h is None or best_inliers is None:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=int(len(xs)),
            n_floor_matches=len(src_local),
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="dense_flow",
        )

    return PairwiseStitch(
        i=i,
        j=j,
        n_matches=int(len(xs)),
        n_floor_matches=len(src_local),
        n_inliers=int(best_inliers.sum()),
        rmse_m=float(best_rmse),
        estimator="dense_flow",
        transform_i_from_j=best_h,
        src_uv_j=src_uv_arr,
        dst_uv_i=dst_uv_arr,
        inlier_mask=best_inliers,
    )


def _estimate_pairwise_stitch_image_homography(
    *,
    i: int,
    j: int,
    img_i_gray: np.ndarray,
    img_j_gray: np.ndarray,
    geom_i: LocalFloorGeometry,
    geom_j: LocalFloorGeometry,
) -> PairwiseStitch:
    import cv2

    orb = cv2.ORB_create(nfeatures=8000, fastThreshold=5)
    kp_i, des_i = orb.detectAndCompute(img_i_gray, None)
    kp_j, des_j = orb.detectAndCompute(img_j_gray, None)
    if des_i is None or des_j is None or not kp_i or not kp_j:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=0,
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="image_homography",
        )

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = bf.knnMatch(des_j, des_i, k=2)  # query: j, train: i
    good: list[object] = []
    for m_n in knn:
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < 0.78 * n.distance:
            good.append(m)
    if len(good) < 12:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(good),
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="image_homography",
        )

    src_px = np.asarray([kp_j[m.queryIdx].pt for m in good], dtype=np.float32)
    dst_px = np.asarray([kp_i[m.trainIdx].pt for m in good], dtype=np.float32)
    h_px, inlier_px = cv2.findHomography(
        srcPoints=src_px,
        dstPoints=dst_px,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=7000,
        confidence=0.995,
    )
    if h_px is None or inlier_px is None or int(inlier_px.sum()) < 8:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(good),
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="image_homography",
        )

    h, w = img_j_gray.shape[:2]
    floor_j = np.asarray(geom_j.valid_floor_mask, dtype=bool)
    ys, xs = np.where(floor_j)
    if len(xs) < 200:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(good),
            n_floor_matches=0,
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="image_homography",
        )

    stride = max(3, int(np.sqrt(max(1, len(xs) // 2400))))
    xs = xs[::stride]
    ys = ys[::stride]
    src_uv_j_arr = np.stack([xs.astype(np.float32), ys.astype(np.float32)], axis=1)
    warped = cv2.perspectiveTransform(src_uv_j_arr.reshape(-1, 1, 2), h_px).reshape(-1, 2)

    src_local: list[np.ndarray] = []
    dst_local: list[np.ndarray] = []
    src_uv_j: list[tuple[float, float]] = []
    dst_uv_i: list[tuple[float, float]] = []

    for k in range(src_uv_j_arr.shape[0]):
        xj = float(src_uv_j_arr[k, 0])
        yj = float(src_uv_j_arr[k, 1])
        xi = float(warped[k, 0])
        yi = float(warped[k, 1])
        if xi < 0 or xi >= (w - 1) or yi < 0 or yi >= (h - 1):
            continue
        pj = _project_pixel_to_local_floor(x=xj, y=yj, geom=geom_j)
        if pj is None:
            continue
        pi = _project_pixel_to_local_floor(x=xi, y=yi, geom=geom_i)
        if pi is None:
            continue
        src_local.append(pj)
        dst_local.append(pi)
        src_uv_j.append((xj, yj))
        dst_uv_i.append((xi, yi))

    if len(src_local) < 10:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(good),
            n_floor_matches=len(src_local),
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="image_homography",
        )

    src = np.asarray(src_local, dtype=np.float32)
    dst = np.asarray(dst_local, dtype=np.float32)
    src_uv_arr = np.asarray(src_uv_j, dtype=np.float32)
    dst_uv_arr = np.asarray(dst_uv_i, dtype=np.float32)
    best_h, best_inliers, best_rmse = _fit_local_affine_with_ransac(src, dst)
    if best_h is None or best_inliers is None:
        return PairwiseStitch(
            i=i,
            j=j,
            n_matches=len(good),
            n_floor_matches=len(src_local),
            n_inliers=0,
            rmse_m=float("inf"),
            estimator="image_homography",
        )

    return PairwiseStitch(
        i=i,
        j=j,
        n_matches=len(good),
        n_floor_matches=len(src_local),
        n_inliers=int(best_inliers.sum()),
        rmse_m=float(best_rmse),
        estimator="image_homography",
        transform_i_from_j=best_h,
        src_uv_j=src_uv_arr,
        dst_uv_i=dst_uv_arr,
        inlier_mask=best_inliers,
    )


def _global_stitch_transforms(
    *,
    n_images: int,
    pairwise: list[PairwiseStitch],
    anchor: int,
) -> dict[int, np.ndarray]:
    parent = list(range(n_images))
    rank = [0 for _ in range(n_images)]

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> bool:
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1
        return True

    edges = [p for p in pairwise if p.transform_i_from_j is not None and p.n_inliers >= 6]
    edges.sort(key=lambda e: (e.n_inliers, -e.rmse_m), reverse=True)
    mst: list[PairwiseStitch] = []
    for e in edges:
        if _union(e.i, e.j):
            mst.append(e)

    adj: dict[int, list[tuple[int, np.ndarray]]] = {k: [] for k in range(n_images)}
    for e in mst:
        assert e.transform_i_from_j is not None
        h_i_from_j = e.transform_i_from_j
        h_j_from_i = _safe_inv3x3(h_i_from_j)
        if h_j_from_i is None:
            continue
        adj[e.i].append((e.j, h_i_from_j))
        adj[e.j].append((e.i, h_j_from_i))

    transforms: dict[int, np.ndarray] = {anchor: np.eye(3, dtype=np.float64)}
    stack = [anchor]
    while stack:
        i = stack.pop()
        h_global_from_i = transforms[i]
        for j, h_i_from_j in adj[i]:
            if j in transforms:
                continue
            transforms[j] = h_global_from_i @ h_i_from_j
            stack.append(j)
    return transforms


def _angle_dist_mod_pi(a: np.ndarray, b: float) -> np.ndarray:
    d = np.abs(a - b)
    d = np.mod(d, np.pi)
    return np.minimum(d, np.pi - d)


def _estimate_manhattan_theta_from_boundary(coords: np.ndarray) -> tuple[float, float]:
    """
    Estimate dominant Manhattan axis angle from polygon boundary segments.
    Returns (theta_rad, confidence in [0, 1]).
    """
    if coords.shape[0] < 4:
        return 0.0, 0.0
    seg = coords[1:] - coords[:-1]
    seg_len = np.linalg.norm(seg, axis=1)
    keep = seg_len > 1e-5
    if int(keep.sum()) < 2:
        return 0.0, 0.0
    seg = seg[keep]
    seg_len = seg_len[keep]
    ang = np.mod(np.arctan2(seg[:, 1], seg[:, 0]), np.pi)

    grid = np.linspace(0.0, 0.5 * np.pi, 181, endpoint=False)
    sigma = np.deg2rad(11.0)
    best_theta = 0.0
    best_score = -1.0
    total = float(seg_len.sum())
    for theta in grid:
        err0 = _angle_dist_mod_pi(ang, float(theta))
        err1 = _angle_dist_mod_pi(ang, float(theta + 0.5 * np.pi))
        err = np.minimum(err0, err1)
        score = float(np.sum(seg_len * np.exp(-0.5 * (err / sigma) ** 2)))
        if score > best_score:
            best_score = score
            best_theta = float(theta)

    conf = 0.0 if total <= 0 else float(np.clip(best_score / total, 0.0, 1.0))
    return best_theta, conf


def _oriented_bbox_from_theta(coords: np.ndarray, theta: float):
    from shapely.geometry import Polygon

    u = np.array([np.cos(theta), np.sin(theta)], dtype=np.float64)
    v = np.array([-u[1], u[0]], dtype=np.float64)
    pu = coords @ u
    pv = coords @ v
    lo_u, hi_u = float(np.min(pu)), float(np.max(pu))
    lo_v, hi_v = float(np.min(pv)), float(np.max(pv))
    corners_uv = np.array(
        [
            [lo_u, lo_v],
            [hi_u, lo_v],
            [hi_u, hi_v],
            [lo_u, hi_v],
            [lo_u, lo_v],
        ],
        dtype=np.float64,
    )
    corners_xy = corners_uv[:, [0]] * u[None, :] + corners_uv[:, [1]] * v[None, :]
    poly = Polygon(corners_xy)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def _anchored_oriented_bbox_from_theta(coords: np.ndarray, theta: float):
    from shapely.geometry import Polygon

    u = np.array([np.cos(theta), np.sin(theta)], dtype=np.float64)
    v = np.array([-u[1], u[0]], dtype=np.float64)
    pu = coords @ u
    pv = coords @ v
    lo_u, hi_u = float(np.min(pu)), float(np.max(pu))
    lo_v, hi_v = float(np.min(pv)), float(np.max(pv))

    pts0 = coords[:-1]
    pts1 = coords[1:]
    seg = pts1 - pts0
    seg_len = np.linalg.norm(seg, axis=1)
    keep = seg_len > 1e-5
    if int(keep.sum()) < 2:
        poly = _oriented_bbox_from_theta(coords, theta)
        return poly, {
            "anchored_u": False,
            "anchored_v": False,
            "u_anchor_side": None,
            "v_anchor_side": None,
            "u_anchor_conf": 0.0,
            "v_anchor_conf": 0.0,
        }

    pts0 = pts0[keep]
    pts1 = pts1[keep]
    seg_len = seg_len[keep]
    ang = np.mod(np.arctan2((pts1 - pts0)[:, 1], (pts1 - pts0)[:, 0]), np.pi)
    err_u = _angle_dist_mod_pi(ang, float(theta))
    err_v = _angle_dist_mod_pi(ang, float(theta + 0.5 * np.pi))

    pu0 = pts0 @ u
    pu1 = pts1 @ u
    pv0 = pts0 @ v
    pv1 = pts1 @ v

    th = np.deg2rad(14.0)

    def _pick_anchor(mask: np.ndarray, values0: np.ndarray, values1: np.ndarray) -> tuple[float | None, float]:
        idx = np.where(mask)[0]
        if idx.size == 0:
            return None, 0.0
        local_len = seg_len[idx]
        k = int(idx[np.argmax(local_len)])
        val = 0.5 * float(values0[k] + values1[k])
        conf = float(np.max(local_len) / max(1e-9, float(seg_len.sum())))
        return val, conf

    # u-extents are supported by edges parallel to v, and vice versa.
    u_anchor, u_conf = _pick_anchor(err_v < th, pu0, pu1)
    v_anchor, v_conf = _pick_anchor(err_u < th, pv0, pv1)

    tol = 0.08

    def _apply_anchor(lo: float, hi: float, anchor: float | None) -> tuple[float, float, bool, str | None]:
        if anchor is None:
            return lo, hi, False, None
        if abs(anchor - lo) <= abs(anchor - hi):
            # Candidate for the lower support line.
            if anchor > lo + tol:
                return lo, hi, False, None
            return min(lo, anchor), hi, True, "lo"
        # Candidate for the upper support line.
        if anchor < hi - tol:
            return lo, hi, False, None
        return lo, max(hi, anchor), True, "hi"

    lo_u2, hi_u2, anchored_u, side_u = _apply_anchor(lo_u, hi_u, u_anchor)
    lo_v2, hi_v2, anchored_v, side_v = _apply_anchor(lo_v, hi_v, v_anchor)

    corners_uv = np.array(
        [
            [lo_u2, lo_v2],
            [hi_u2, lo_v2],
            [hi_u2, hi_v2],
            [lo_u2, hi_v2],
            [lo_u2, lo_v2],
        ],
        dtype=np.float64,
    )
    corners_xy = corners_uv[:, [0]] * u[None, :] + corners_uv[:, [1]] * v[None, :]
    poly = Polygon(corners_xy)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly, {
        "anchored_u": bool(anchored_u),
        "anchored_v": bool(anchored_v),
        "u_anchor_side": side_u,
        "v_anchor_side": side_v,
        "u_anchor_conf": float(u_conf),
        "v_anchor_conf": float(v_conf),
    }


def _completion_rectangle(poly, completion_geometry: str) -> tuple[object, dict[str, object]]:
    strategy = (completion_geometry or "manhattan-rect").strip().lower()
    if strategy == "min-rect":
        rect = poly.minimum_rotated_rectangle
        return rect, {"strategy": "min-rect", "axis_confidence": None, "theta_rad": None}

    simp = poly.simplify(0.05, preserve_topology=True)
    if simp is None or simp.is_empty or not hasattr(simp, "exterior"):
        simp = poly
    coords = np.asarray(simp.exterior.coords, dtype=np.float64)
    if coords.shape[0] < 4:
        coords = np.asarray(poly.exterior.coords, dtype=np.float64)
    theta, conf = _estimate_manhattan_theta_from_boundary(coords)
    rect, anchor_info = _anchored_oriented_bbox_from_theta(coords, theta)
    if rect.is_empty or float(rect.area) <= 0:
        rect = poly.minimum_rotated_rectangle
        return rect, {
            "strategy": "min-rect-fallback",
            "axis_confidence": float(conf),
            "theta_rad": float(theta),
        }
    return rect, {
        "strategy": "manhattan-rect",
        "axis_confidence": float(conf),
        "theta_rad": float(theta),
        "anchor": anchor_info,
    }


def _save_stitch_artifacts(
    *,
    polygons: list[np.ndarray],
    union_coords: np.ndarray | None,
    rect_coords: np.ndarray | None,
    out_dir: Path,
) -> dict[str, str]:
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    canvas = np.full((1200, 1200, 3), 255, dtype=np.uint8)

    pts_all = [p for p in polygons if p is not None and p.size > 0]
    if union_coords is not None and union_coords.size > 0:
        pts_all.append(union_coords)
    if rect_coords is not None and rect_coords.size > 0:
        pts_all.append(rect_coords)
    if not pts_all:
        path = out_dir / "stitched_floor.png"
        cv2.imwrite(str(path), canvas)
        return {"stitched_floor": str(path)}

    allp = np.concatenate(pts_all, axis=0)
    xmin, ymin = np.min(allp, axis=0)
    xmax, ymax = np.max(allp, axis=0)
    sx = 1000.0 / max(1e-6, float(xmax - xmin))
    sy = 1000.0 / max(1e-6, float(ymax - ymin))
    s = min(sx, sy)
    tx = 600.0 - 0.5 * s * float(xmin + xmax)
    ty = 600.0 + 0.5 * s * float(ymin + ymax)

    def _to_uv(coords: np.ndarray) -> np.ndarray:
        uv = np.empty_like(coords)
        uv[:, 0] = s * coords[:, 0] + tx
        uv[:, 1] = -s * coords[:, 1] + ty
        return np.round(uv).astype(np.int32).reshape((-1, 1, 2))

    colors = [(180, 180, 180), (180, 130, 80), (80, 160, 220), (160, 120, 220), (80, 170, 120), (220, 150, 80)]
    for i, poly in enumerate(polygons):
        if poly is None or poly.size == 0:
            continue
        cv2.polylines(canvas, [_to_uv(poly)], isClosed=True, color=colors[i % len(colors)], thickness=2)

    if union_coords is not None and union_coords.size > 0:
        cv2.polylines(canvas, [_to_uv(union_coords)], isClosed=True, color=(20, 20, 220), thickness=3)
    if rect_coords is not None and rect_coords.size > 0:
        cv2.polylines(canvas, [_to_uv(rect_coords)], isClosed=True, color=(30, 170, 40), thickness=3)

    path = out_dir / "stitched_floor.png"
    cv2.imwrite(str(path), canvas)
    return {"stitched_floor": str(path)}


def _save_pairwise_match_artifacts(
    *,
    images: list[Path],
    pairwise: list[PairwiseStitch],
    out_dir: Path,
    max_pairs: int = 8,
    max_lines: int = 220,
) -> list[dict[str, object]]:
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    pairs = sorted(pairwise, key=lambda p: p.n_inliers, reverse=True)[:max_pairs]
    rng = np.random.default_rng(7)

    for p in pairs:
        if p.src_uv_j is None or p.dst_uv_i is None or p.src_uv_j.size == 0:
            continue
        img_i = cv2.imread(str(images[p.i]), cv2.IMREAD_COLOR)
        img_j = cv2.imread(str(images[p.j]), cv2.IMREAD_COLOR)
        if img_i is None or img_j is None:
            continue

        hi, wi = img_i.shape[:2]
        hj, wj = img_j.shape[:2]
        h = max(hi, hj)
        w = wi + wj
        canvas = np.full((h, w, 3), 0, dtype=np.uint8)
        canvas[:hi, :wi] = img_i
        canvas[:hj, wi : wi + wj] = img_j

        src = np.asarray(p.src_uv_j, dtype=np.float32)
        dst = np.asarray(p.dst_uv_i, dtype=np.float32)
        inliers = (
            np.asarray(p.inlier_mask, dtype=bool)
            if p.inlier_mask is not None and len(p.inlier_mask) == len(src)
            else np.zeros((len(src),), dtype=bool)
        )

        idx = np.arange(len(src))
        if len(idx) > max_lines:
            idx = rng.choice(idx, size=max_lines, replace=False)

        for k in idx:
            a = (int(round(dst[k, 0])), int(round(dst[k, 1])))
            b = (int(round(src[k, 0] + wi)), int(round(src[k, 1])))
            color = (30, 220, 30) if inliers[k] else (120, 120, 120)
            thickness = 1 if inliers[k] else 1
            cv2.line(canvas, a, b, color, thickness, cv2.LINE_AA)
            if inliers[k]:
                cv2.circle(canvas, a, 2, (30, 220, 30), -1, cv2.LINE_AA)
                cv2.circle(canvas, b, 2, (30, 220, 30), -1, cv2.LINE_AA)

        label = f"pair {p.i}<->{p.j}  floor_matches={p.n_floor_matches}  inliers={p.n_inliers}  rmse={p.rmse_m:.3f}m"
        cv2.putText(canvas, label, (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(
            canvas,
            f"left=image_i  right=image_j  estimator={p.estimator}",
            (16, 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )

        out_path = out_dir / f"pair_{p.i:02d}_{p.j:02d}.jpg"
        cv2.imwrite(str(out_path), canvas)
        rows.append(
            {
                "i": int(p.i),
                "j": int(p.j),
                "inliers": int(p.n_inliers),
                "floor_matches": int(p.n_floor_matches),
                "rmse_m": float(p.rmse_m),
                "estimator": str(p.estimator),
                "image": str(out_path),
            }
        )
    return rows


def _save_scene_floor_artifacts(
    *,
    points2d: np.ndarray,
    point_sources: np.ndarray | None,
    poly_coords: np.ndarray | None,
    rect_coords: np.ndarray | None,
    cam2d: np.ndarray | None,
    out_dir: Path,
) -> dict[str, str]:
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    canvas = np.full((1200, 1200, 3), 255, dtype=np.uint8)
    components = np.full((1200, 1200, 3), 255, dtype=np.uint8)

    pts_all = []
    if points2d is not None and points2d.size > 0:
        pts_all.append(points2d)
    if poly_coords is not None and poly_coords.size > 0:
        pts_all.append(poly_coords)
    if rect_coords is not None and rect_coords.size > 0:
        pts_all.append(rect_coords)
    if cam2d is not None and cam2d.size > 0:
        pts_all.append(cam2d)

    if not pts_all:
        path_a = out_dir / "scene_floor_topdown.png"
        path_b = out_dir / "scene_floor_components.png"
        cv2.imwrite(str(path_a), canvas)
        cv2.imwrite(str(path_b), components)
        return {"scene_floor_topdown": str(path_a), "scene_floor_components": str(path_b)}

    allp = np.concatenate(pts_all, axis=0)
    xmin, ymin = np.min(allp, axis=0)
    xmax, ymax = np.max(allp, axis=0)
    sx = 1000.0 / max(1e-6, float(xmax - xmin))
    sy = 1000.0 / max(1e-6, float(ymax - ymin))
    s = min(sx, sy)
    tx = 600.0 - 0.5 * s * float(xmin + xmax)
    ty = 600.0 + 0.5 * s * float(ymin + ymax)

    def _to_uv(coords: np.ndarray) -> np.ndarray:
        uv = np.empty_like(coords)
        uv[:, 0] = s * coords[:, 0] + tx
        uv[:, 1] = -s * coords[:, 1] + ty
        return np.round(uv).astype(np.int32)

    draw_pts = points2d
    draw_src = point_sources
    if draw_pts is not None and draw_pts.shape[0] > 180_000:
        rng = np.random.default_rng(11)
        idx = rng.choice(draw_pts.shape[0], size=180_000, replace=False)
        draw_pts = draw_pts[idx]
        if draw_src is not None and draw_src.shape[0] == points2d.shape[0]:
            draw_src = draw_src[idx]

    palette = np.array(
        [
            [220, 120, 40],
            [40, 100, 220],
            [60, 180, 70],
            [160, 80, 220],
            [50, 170, 200],
            [180, 150, 40],
            [200, 80, 100],
        ],
        dtype=np.uint8,
    )

    if draw_pts is not None and draw_pts.size > 0:
        uv = _to_uv(draw_pts)
        uv[:, 0] = np.clip(uv[:, 0], 0, canvas.shape[1] - 1)
        uv[:, 1] = np.clip(uv[:, 1], 0, canvas.shape[0] - 1)
        if draw_src is None or draw_src.shape[0] != draw_pts.shape[0]:
            canvas[uv[:, 1], uv[:, 0]] = palette[0]
        else:
            colors = palette[np.mod(draw_src.astype(np.int32), len(palette))]
            canvas[uv[:, 1], uv[:, 0]] = colors

    def _draw_poly(coords: np.ndarray, color: tuple[int, int, int], thickness: int):
        if coords is None or coords.size == 0:
            return
        uv = _to_uv(coords).reshape((-1, 1, 2))
        cv2.polylines(canvas, [uv], isClosed=True, color=color, thickness=thickness)
        cv2.polylines(components, [uv], isClosed=True, color=color, thickness=thickness)

    source_polys: list[tuple[int, np.ndarray]] = []
    if points2d is not None and points2d.size > 0 and point_sources is not None and point_sources.shape[0] == points2d.shape[0]:
        for src_id in sorted(np.unique(point_sources).tolist()):
            src_pts = points2d[point_sources == src_id]
            if src_pts.shape[0] < 500:
                continue
            if src_pts.shape[0] > 50_000:
                rng = np.random.default_rng(100 + int(src_id))
                idx = rng.choice(src_pts.shape[0], size=50_000, replace=False)
                src_pts = src_pts[idx]
            try:
                alpha = _auto_alpha(src_pts)
                src_poly = _alpha_shape(src_pts, alpha)
            except Exception:
                continue
            if getattr(src_poly, "geom_type", "") == "MultiPolygon":
                src_poly = max(list(src_poly.geoms), key=lambda g: g.area)
            if not hasattr(src_poly, "exterior"):
                continue
            src_coords = np.asarray(src_poly.exterior.coords, dtype=np.float32)
            if src_coords.shape[0] < 4:
                continue
            source_polys.append((int(src_id), src_coords))

    if source_polys:
        overlay = components.copy()
        for src_id, src_coords in source_polys:
            src_uv = _to_uv(src_coords).reshape((-1, 1, 2))
            c = palette[int(src_id) % len(palette)]
            color = (int(c[0]), int(c[1]), int(c[2]))
            cv2.fillPoly(overlay, [src_uv], color=color)
            cv2.polylines(canvas, [src_uv], isClosed=True, color=color, thickness=2)
            center = np.mean(_to_uv(src_coords), axis=0)
            label_pt = (int(center[0]) + 8, int(center[1]) - 8)
            cv2.putText(canvas, f"view {src_id}", label_pt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.22, components, 0.78, 0, components)

    _draw_poly(poly_coords, (20, 20, 220), 3)
    _draw_poly(rect_coords, (30, 170, 40), 3)

    if cam2d is not None and cam2d.size > 0:
        cam_uv = _to_uv(cam2d)
        for i, p in enumerate(cam_uv):
            x, y = int(p[0]), int(p[1])
            cv2.circle(canvas, (x, y), 8, (20, 20, 20), 2, cv2.LINE_AA)
            cv2.circle(components, (x, y), 8, (20, 20, 20), 2, cv2.LINE_AA)
            cv2.putText(
                canvas,
                str(i),
                (x + 10, y - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (40, 40, 40),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                components,
                str(i),
                (x + 10, y - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (40, 40, 40),
                2,
                cv2.LINE_AA,
            )

    path_a = out_dir / "scene_floor_topdown.png"
    path_b = out_dir / "scene_floor_components.png"
    cv2.imwrite(str(path_a), canvas)
    cv2.imwrite(str(path_b), components)
    return {"scene_floor_topdown": str(path_a), "scene_floor_components": str(path_b)}


def _write_pointcloud_ply(path: Path, points: np.ndarray, colors: np.ndarray | None = None):
    pts = np.asarray(points, dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points must be Nx3")
    finite = np.isfinite(pts).all(axis=1)
    pts = pts[finite]
    if pts.shape[0] == 0:
        return

    if colors is None:
        cols = np.full((pts.shape[0], 3), 180, dtype=np.uint8)
    else:
        cols = np.asarray(colors, dtype=np.uint8)
        if cols.ndim != 2 or cols.shape[1] != 3:
            raise ValueError("colors must be Nx3")
        cols = cols[finite]
        if cols.shape[0] != pts.shape[0]:
            raise ValueError("colors length mismatch")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("ply\n")
        f.write("format ascii 1.0\n")
        f.write(f"element vertex {pts.shape[0]}\n")
        f.write("property float x\n")
        f.write("property float y\n")
        f.write("property float z\n")
        f.write("property uchar red\n")
        f.write("property uchar green\n")
        f.write("property uchar blue\n")
        f.write("end_header\n")
        for p, c in zip(pts, cols):
            f.write(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {int(c[0])} {int(c[1])} {int(c[2])}\n")


def _sample_points(points: np.ndarray, *, max_points: int, seed: int) -> np.ndarray:
    pts = np.asarray(points)
    if pts.shape[0] <= max_points:
        return pts
    rng = np.random.default_rng(seed)
    idx = rng.choice(pts.shape[0], size=max_points, replace=False)
    return pts[idx]


def _save_scene_html_viewer(
    *,
    out_path: Path,
    scene_points: np.ndarray | None,
    floor_points: np.ndarray | None,
    floor_point_sources: np.ndarray | None,
    cam_centers: np.ndarray | None,
) -> str | None:
    traces: list[dict[str, object]] = []

    if scene_points is not None and scene_points.size > 0:
        scene = _sample_points(np.asarray(scene_points, dtype=np.float32), max_points=90_000, seed=31)
        traces.append(
            {
                "type": "scatter3d",
                "mode": "markers",
                "name": "scene points",
                "x": np.round(scene[:, 0], 4).tolist(),
                "y": np.round(scene[:, 1], 4).tolist(),
                "z": np.round(scene[:, 2], 4).tolist(),
                "marker": {"size": 1.0, "color": "rgba(140,140,140,0.35)"},
            }
        )

    if floor_points is not None and floor_points.size > 0:
        floor = np.asarray(floor_points, dtype=np.float32)
        src = (
            np.asarray(floor_point_sources, dtype=np.int32)
            if floor_point_sources is not None and floor_point_sources.shape[0] == floor.shape[0]
            else None
        )
        if floor.shape[0] > 120_000:
            rng = np.random.default_rng(32)
            idx = rng.choice(floor.shape[0], size=120_000, replace=False)
            floor = floor[idx]
            if src is not None:
                src = src[idx]
        if src is None:
            traces.append(
                {
                    "type": "scatter3d",
                    "mode": "markers",
                    "name": "fused floor",
                    "x": np.round(floor[:, 0], 4).tolist(),
                    "y": np.round(floor[:, 1], 4).tolist(),
                    "z": np.round(floor[:, 2], 4).tolist(),
                    "marker": {"size": 1.3, "color": "rgba(230,120,35,0.85)"},
                }
            )
        else:
            palette = ["#e67e22", "#3f7ad8", "#2ca25f", "#9b59b6", "#17a2b8", "#b7950b", "#d35454"]
            for sid in sorted(np.unique(src).tolist()):
                pts = floor[src == sid]
                if pts.shape[0] == 0:
                    continue
                traces.append(
                    {
                        "type": "scatter3d",
                        "mode": "markers",
                        "name": f"floor view {sid}",
                        "x": np.round(pts[:, 0], 4).tolist(),
                        "y": np.round(pts[:, 1], 4).tolist(),
                        "z": np.round(pts[:, 2], 4).tolist(),
                        "marker": {"size": 1.3, "color": palette[int(sid) % len(palette)]},
                    }
                )

    if cam_centers is not None and cam_centers.size > 0:
        cams = np.asarray(cam_centers, dtype=np.float32)
        traces.append(
            {
                "type": "scatter3d",
                "mode": "markers+text",
                "name": "camera centers",
                "x": np.round(cams[:, 0], 4).tolist(),
                "y": np.round(cams[:, 1], 4).tolist(),
                "z": np.round(cams[:, 2], 4).tolist(),
                "text": [str(i) for i in range(cams.shape[0])],
                "textposition": "top center",
                "marker": {"size": 6, "color": "#d62728"},
            }
        )

    if not traces:
        return None

    payload = json.dumps(traces)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Scene Viewer</title>
  <style>
    html, body {{ height: 100%; margin: 0; background: #f5f6f7; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    #wrap {{ display: flex; flex-direction: column; height: 100%; }}
    #bar {{ padding: 10px 14px; font-size: 13px; color: #222; border-bottom: 1px solid #ddd; background: #fff; }}
    #plot {{ flex: 1; min-height: 0; }}
    button {{ margin-right: 8px; border: 1px solid #ccc; background: #fff; padding: 5px 10px; border-radius: 6px; cursor: pointer; }}
  </style>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div id="wrap">
    <div id="bar">
      <button onclick="setTopDown()">Top Down</button>
      <button onclick="setIsometric()">Isometric</button>
      Rotate/zoom with mouse. Use legend to toggle traces.
    </div>
    <div id="plot"></div>
  </div>
  <script>
    const traces = {payload};
    const layout = {{
      margin: {{ l: 0, r: 0, t: 0, b: 0 }},
      scene: {{
        aspectmode: "data",
        xaxis: {{ title: "X (m)", showbackground: false }},
        yaxis: {{ title: "Y (m)", showbackground: false }},
        zaxis: {{ title: "Z (m)", showbackground: false }},
        camera: {{
          eye: {{ x: 1.6, y: 1.6, z: 1.1 }},
          up: {{ x: 0, y: 0, z: 1 }}
        }}
      }},
      showlegend: true,
      legend: {{ orientation: "h", y: 0.98, x: 0.01 }}
    }};
    Plotly.newPlot("plot", traces, layout, {{ responsive: true, displaylogo: false }});
    function setTopDown() {{
      Plotly.relayout("plot", {{
        "scene.camera.eye": {{ x: 0.001, y: 0.001, z: 2.8 }},
        "scene.camera.up": {{ x: 0, y: 1, z: 0 }}
      }});
    }}
    function setIsometric() {{
      Plotly.relayout("plot", {{
        "scene.camera.eye": {{ x: 1.6, y: 1.6, z: 1.1 }},
        "scene.camera.up": {{ x: 0, y: 0, z: 1 }}
      }});
    }}
  </script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


def _project_world_to_image(
    points_world: np.ndarray,
    cam2world: np.ndarray,
    intrinsics: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(points_world, dtype=np.float64)
    R = np.asarray(cam2world[:3, :3], dtype=np.float64)
    t = np.asarray(cam2world[:3, 3], dtype=np.float64)
    K = np.asarray(intrinsics[:3, :3], dtype=np.float64)

    # cam2world is camera->world with row-vector convention:
    #   X_world = X_cam @ R.T + t
    # so inverse is:
    #   X_cam = (X_world - t) @ R
    cam = (pts - t[None, :]) @ R
    z = cam[:, 2]
    valid = np.isfinite(cam).all(axis=1) & (z > 1e-5)
    uv = np.full((pts.shape[0], 2), np.nan, dtype=np.float64)
    if np.any(valid):
        x = cam[valid, 0] / z[valid]
        y = cam[valid, 1] / z[valid]
        uv[valid, 0] = K[0, 0] * x + K[0, 2]
        uv[valid, 1] = K[1, 1] * y + K[1, 2]
    return uv, valid


def _save_scene_reprojection_artifacts(
    *,
    images: list[Path],
    recon: Dust3RRecon,
    cam2world_metric: np.ndarray,
    floor_points_world: np.ndarray,
    floor_point_sources: np.ndarray,
    floor_poly_coords_2d: np.ndarray | None,
    floor_plane: Plane,
    out_dir: Path,
) -> dict[str, object]:
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, object] = {}

    palette = np.array(
        [
            [230, 130, 40],
            [45, 110, 230],
            [70, 185, 80],
            [175, 90, 230],
            [55, 175, 205],
            [185, 160, 55],
            [220, 90, 120],
        ],
        dtype=np.uint8,
    )

    floor_pts = np.asarray(floor_points_world, dtype=np.float32)
    floor_src = np.asarray(floor_point_sources, dtype=np.int32)
    if floor_pts.shape[0] > 180_000:
        rng = np.random.default_rng(41)
        idx = rng.choice(floor_pts.shape[0], size=180_000, replace=False)
        floor_pts = floor_pts[idx]
        floor_src = floor_src[idx]

    poly3d = None
    if floor_poly_coords_2d is not None and floor_poly_coords_2d.size > 0:
        n = np.asarray(floor_plane.normal, dtype=np.float64)
        u, v = _plane_basis(n)
        p0 = -float(floor_plane.d) * n
        coords = np.asarray(floor_poly_coords_2d, dtype=np.float64)
        poly3d = p0[None, :] + coords[:, 0:1] * u[None, :] + coords[:, 1:2] * v[None, :]

    gallery_rows: list[np.ndarray] = []
    overlay_paths: list[str] = []
    cross_paths: list[str] = []
    boundary_paths: list[str] = []
    view_data: list[dict[str, object]] = []
    n_views = min(
        len(images),
        len(recon.depthmaps),
        cam2world_metric.shape[0],
        recon.intrinsics.shape[0],
    )
    for i in range(n_views):
        h, w = recon.depthmaps[i].shape[:2]
        img = cv2.imread(str(images[i]), cv2.IMREAD_COLOR)
        if img is None:
            continue
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
        overlay = img.copy()
        overlay_cross = img.copy()
        overlay_boundary = img.copy()

        uv, valid = _project_world_to_image(floor_pts, cam2world_metric[i], recon.intrinsics[i])
        in_bounds = (
            valid
            & np.isfinite(uv[:, 0])
            & np.isfinite(uv[:, 1])
            & (uv[:, 0] >= 0)
            & (uv[:, 0] < w)
            & (uv[:, 1] >= 0)
            & (uv[:, 1] < h)
        )
        boundary_uv = None
        boundary_valid = None
        if poly3d is not None and poly3d.shape[0] >= 4:
            boundary_uv, boundary_valid = _project_world_to_image(poly3d, cam2world_metric[i], recon.intrinsics[i])
        inside_global_boundary = in_bounds.copy()
        if boundary_uv is not None and boundary_valid is not None and int(np.sum(boundary_valid)) >= 4:
            pxy = np.round(boundary_uv[boundary_valid]).astype(np.int32).reshape((-1, 1, 2))
            bmask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(bmask, [pxy], 255)
            inside_global_boundary = np.zeros_like(in_bounds, dtype=bool)
            if int(np.sum(in_bounds)) > 0:
                pts_ib = uv[in_bounds]
                px_ib = np.clip(np.round(pts_ib[:, 0]).astype(np.int32), 0, w - 1)
                py_ib = np.clip(np.round(pts_ib[:, 1]).astype(np.int32), 0, h - 1)
                idx_ib = np.where(in_bounds)[0]
                inside_global_boundary[idx_ib] = bmask[py_ib, px_ib] > 0

        def _draw_src_points(dst: np.ndarray, extra_mask: np.ndarray):
            for sid in sorted(np.unique(floor_src).tolist()):
                sid_mask = in_bounds & extra_mask & (floor_src == int(sid))
                if int(np.sum(sid_mask)) == 0:
                    continue
                pts_sid = uv[sid_mask]
                px = np.clip(np.round(pts_sid[:, 0]).astype(np.int32), 0, w - 1)
                py = np.clip(np.round(pts_sid[:, 1]).astype(np.int32), 0, h - 1)
                m = np.zeros((h, w), dtype=np.uint8)
                m[py, px] = 255
                m = cv2.dilate(m, np.ones((3, 3), dtype=np.uint8), iterations=1)
                c = palette[int(sid) % len(palette)]
                color_img = np.zeros_like(dst, dtype=np.uint8)
                color_img[:, :] = (int(c[2]), int(c[1]), int(c[0]))  # BGR
                alpha = 0.45
                dst[:] = np.where(m[..., None] > 0, (1 - alpha) * dst + alpha * color_img, dst).astype(np.uint8)

        _draw_src_points(overlay, inside_global_boundary)
        _draw_src_points(overlay_cross, (floor_src != i) & inside_global_boundary)

        def _draw_boundary(dst: np.ndarray):
            if boundary_uv is None or boundary_valid is None:
                return
            if int(np.sum(boundary_valid)) >= 4:
                pxy = np.round(boundary_uv[boundary_valid]).astype(np.int32).reshape((-1, 1, 2))
                cv2.polylines(dst, [pxy], isClosed=True, color=(20, 20, 220), thickness=2, lineType=cv2.LINE_AA)

        _draw_boundary(overlay)
        _draw_boundary(overlay_cross)
        _draw_boundary(overlay_boundary)

        for sid in sorted(np.unique(floor_src).tolist()):
            pass

        panel = np.hstack([img, overlay_boundary, overlay_cross])
        cv2.putText(panel, "input", (16, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(panel, "input", (16, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (30, 30, 30), 1, cv2.LINE_AA)
        cv2.putText(
            panel,
            "boundary only (red)",
            (w + 16, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            panel,
            "boundary only (red)",
            (w + 16, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            panel,
            "cross-view add",
            (2 * w + 16, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            panel,
            "cross-view add",
            (2 * w + 16, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )

        out_path = out_dir / f"reproject_view_{i:02d}.png"
        cv2.imwrite(str(out_path), panel)
        overlay_paths.append(str(out_path))
        gallery_rows.append(panel)

        cross_path = out_dir / f"reproject_cross_{i:02d}.png"
        cv2.imwrite(str(cross_path), overlay_cross)
        cross_paths.append(str(cross_path))

        boundary_path = out_dir / f"reproject_boundary_{i:02d}.png"
        cv2.imwrite(str(boundary_path), overlay_boundary)
        boundary_paths.append(str(boundary_path))
        view_data.append(
            {
                "img": img,
                "w": w,
                "h": h,
                "uv": uv,
                "valid": valid,
                "in_bounds": in_bounds,
                "boundary_uv": boundary_uv,
                "boundary_valid": boundary_valid,
            }
        )

    if gallery_rows:
        gallery = np.vstack(gallery_rows)
        gallery_path = out_dir / "reproject_gallery.png"
        cv2.imwrite(str(gallery_path), gallery)
        artifacts["reproject_gallery"] = str(gallery_path)
    artifacts["reproject_views"] = overlay_paths
    artifacts["reproject_cross_views"] = cross_paths
    artifacts["reproject_boundary_views"] = boundary_paths

    # Combined floor-space atlas using both views. This keeps the fusion visual
    # coherent without trying to force full-scene planar warps.
    if len(view_data) >= 2 and floor_pts.shape[0] > 0:
        n = np.asarray(floor_plane.normal, dtype=np.float64)
        n_norm = float(np.linalg.norm(n))
        if n_norm > 1e-8:
            n = n / n_norm
        u, v = _plane_basis(n)
        p0 = -float(floor_plane.d) * n
        coords = np.stack(
            [
                np.dot(floor_pts - p0[None, :], u[None, :].T).reshape(-1),
                np.dot(floor_pts - p0[None, :], v[None, :].T).reshape(-1),
            ],
            axis=1,
        )
        valid_coords = np.isfinite(coords).all(axis=1)
        poly2: np.ndarray | None = None
        if floor_poly_coords_2d is not None and floor_poly_coords_2d.size >= 8:
            poly2 = np.asarray(floor_poly_coords_2d, dtype=np.float64)
            if poly2.ndim == 2 and poly2.shape[0] >= 4:
                x = coords[:, 0]
                y = coords[:, 1]
                inside = np.zeros(coords.shape[0], dtype=bool)
                x0 = poly2[-1, 0]
                y0 = poly2[-1, 1]
                for k in range(poly2.shape[0]):
                    x1 = poly2[k, 0]
                    y1 = poly2[k, 1]
                    cross = ((y1 > y) != (y0 > y)) & (
                        x < (x0 - x1) * (y - y1) / ((y0 - y1) + 1e-12) + x1
                    )
                    inside ^= cross
                    x0 = x1
                    y0 = y1
                valid_coords &= inside
        if int(np.sum(valid_coords)) >= 20:
            coords_v = coords[valid_coords]
            min_xy = coords_v.min(axis=0)
            max_xy = coords_v.max(axis=0)
            span_xy = np.maximum(max_xy - min_xy, 1e-6)
            pad_m = 0.12
            max_dim = 2200.0
            px_per_m = min(260.0, max_dim / float(max(span_xy) + 2.0 * pad_m))
            out_w = max(320, int(np.ceil((span_xy[0] + 2.0 * pad_m) * px_per_m)))
            out_h = max(320, int(np.ceil((span_xy[1] + 2.0 * pad_m) * px_per_m)))

            def _coords_to_atlas_xy(c2: np.ndarray) -> np.ndarray:
                x = (c2[:, 0] - min_xy[0] + pad_m) * px_per_m
                y = (max_xy[1] - c2[:, 1] + pad_m) * px_per_m
                return np.stack([x, y], axis=1)

            atlas_acc = np.zeros((out_h, out_w, 3), dtype=np.float32)
            atlas_w = np.zeros((out_h, out_w), dtype=np.float32)
            floor_fill = np.zeros((out_h, out_w), dtype=np.uint8)

            for j, data in enumerate(view_data):
                sel = valid_coords & (floor_src == int(j))
                if int(np.sum(sel)) == 0:
                    continue
                uv_j = np.asarray(data["uv"], dtype=np.float64)[sel]
                inb_j = np.asarray(data["in_bounds"], dtype=bool)[sel]
                c_j = coords[sel]
                good = inb_j & np.isfinite(uv_j).all(axis=1) & np.isfinite(c_j).all(axis=1)
                if int(np.sum(good)) == 0:
                    continue
                uv_j = uv_j[good]
                c_j = c_j[good]

                img = np.asarray(data["img"], dtype=np.uint8)
                h_j, w_j = img.shape[:2]
                px = np.clip(np.round(uv_j[:, 0]).astype(np.int32), 0, w_j - 1)
                py = np.clip(np.round(uv_j[:, 1]).astype(np.int32), 0, h_j - 1)
                col = img[py, px].astype(np.float32)

                atlas_xy = _coords_to_atlas_xy(c_j)
                ax = np.clip(np.round(atlas_xy[:, 0]).astype(np.int32), 0, out_w - 1)
                ay = np.clip(np.round(atlas_xy[:, 1]).astype(np.int32), 0, out_h - 1)
                atlas_acc[ay, ax] += col
                atlas_w[ay, ax] += 1.0
                floor_fill[ay, ax] = 255

            base = np.full((out_h, out_w, 3), 22, dtype=np.uint8)
            nz = atlas_w > 0
            if np.any(nz):
                base[nz] = np.clip(atlas_acc[nz] / atlas_w[nz, None], 0, 255).astype(np.uint8)
                fill_soft = cv2.dilate(floor_fill, np.ones((3, 3), dtype=np.uint8), iterations=2)
                smooth = cv2.GaussianBlur(base, (5, 5), 0)
                use_soft = (fill_soft > 0) & (~nz)
                base[use_soft] = smooth[use_soft]

            mosaic_path = out_dir / "reproject_stitched_mosaic.png"
            cv2.imwrite(str(mosaic_path), base)
            artifacts["reproject_stitched_mosaic"] = str(mosaic_path)

            overlay = base.copy()
            if poly2 is not None and poly2.shape[0] >= 4:
                poly_px = _coords_to_atlas_xy(poly2)
                poly_i = np.round(poly_px).astype(np.int32).reshape(-1, 1, 2)
                if poly_i.shape[0] >= 4:
                    cv2.fillPoly(floor_fill, [poly_i], 255)
                    cv2.polylines(overlay, [poly_i], isClosed=True, color=(20, 20, 220), thickness=2, lineType=cv2.LINE_AA)

            # Show each view's raw floor support as a light boundary for sanity-checking overlap.
            for j, data in enumerate(view_data):
                sel = valid_coords & (floor_src == int(j))
                if int(np.sum(sel)) < 50:
                    continue
                c_j = coords[sel]
                c_j = c_j[np.isfinite(c_j).all(axis=1)]
                if c_j.shape[0] < 20:
                    continue
                if c_j.shape[0] > 3000:
                    step = max(1, c_j.shape[0] // 3000)
                    c_j = c_j[::step]
                pts = _coords_to_atlas_xy(c_j)
                pts_i = np.round(pts).astype(np.int32).reshape(-1, 1, 2)
                hull = cv2.convexHull(pts_i)
                c = palette[j % len(palette)]
                cv2.polylines(
                    overlay,
                    [hull],
                    isClosed=True,
                    color=(int(c[2]), int(c[1]), int(c[0])),
                    thickness=1,
                    lineType=cv2.LINE_AA,
                )

            if np.any(floor_fill > 0):
                color = np.zeros_like(overlay, dtype=np.uint8)
                color[:, :] = (230, 120, 20)
                alpha = 0.22
                use = floor_fill > 0
                overlay[use] = ((1 - alpha) * overlay[use] + alpha * color[use]).astype(np.uint8)

            cv2.putText(
                overlay,
                "combined floor atlas (photo-textured from all views), red=global boundary",
                (14, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                overlay,
                "combined floor atlas (photo-textured from all views), red=global boundary",
                (14, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (20, 20, 20),
                1,
                cv2.LINE_AA,
            )

            panels: list[np.ndarray] = []
            for j in range(min(2, len(view_data))):
                data = view_data[j]
                panel = np.asarray(data["img"], dtype=np.uint8).copy()
                h_j, w_j = panel.shape[:2]
                panel_boundary_mask = None

                b_uv = data.get("boundary_uv")
                b_valid = data.get("boundary_valid")
                if b_uv is not None and b_valid is not None:
                    b_uv = np.asarray(b_uv, dtype=np.float64)
                    b_valid = np.asarray(b_valid, dtype=bool)
                    if int(np.sum(b_valid)) >= 4:
                        poly = np.round(b_uv[b_valid]).astype(np.int32).reshape(-1, 1, 2)
                        panel_boundary_mask = np.zeros((h_j, w_j), dtype=np.uint8)
                        cv2.fillPoly(panel_boundary_mask, [poly], 255)
                        tint = np.zeros_like(panel, dtype=np.uint8)
                        tint[:, :] = (230, 120, 20)
                        use = panel_boundary_mask > 0
                        panel[use] = (0.82 * panel[use] + 0.18 * tint[use]).astype(np.uint8)
                        cv2.polylines(panel, [poly], isClosed=True, color=(20, 20, 220), thickness=2, lineType=cv2.LINE_AA)

                uv_j = np.asarray(data["uv"], dtype=np.float64)
                inb_j = np.asarray(data["in_bounds"], dtype=bool)
                cross = inb_j & (floor_src != j)
                if panel_boundary_mask is not None and int(np.sum(cross)) > 0:
                    idx_cross = np.where(cross)[0]
                    uv_cross = uv_j[idx_cross]
                    px_cross = np.clip(np.round(uv_cross[:, 0]).astype(np.int32), 0, w_j - 1)
                    py_cross = np.clip(np.round(uv_cross[:, 1]).astype(np.int32), 0, h_j - 1)
                    keep_cross = panel_boundary_mask[py_cross, px_cross] > 0
                    cross = np.zeros_like(cross, dtype=bool)
                    cross[idx_cross[keep_cross]] = True
                if int(np.sum(cross)) > 0:
                    if int(np.sum(cross)) > 50_000:
                        idx = np.where(cross)[0]
                        step = max(1, idx.shape[0] // 50_000)
                        keep_idx = idx[::step]
                    else:
                        keep_idx = np.where(cross)[0]
                    pts = uv_j[keep_idx]
                    px = np.clip(np.round(pts[:, 0]).astype(np.int32), 0, w_j - 1)
                    py = np.clip(np.round(pts[:, 1]).astype(np.int32), 0, h_j - 1)
                    m = np.zeros((h_j, w_j), dtype=np.uint8)
                    m[py, px] = 255
                    m = cv2.dilate(m, np.ones((3, 3), dtype=np.uint8), iterations=1)
                    tint = np.zeros_like(panel, dtype=np.uint8)
                    tint[:, :] = (235, 145, 45)
                    panel[:] = np.where(m[..., None] > 0, (0.72 * panel + 0.28 * tint).astype(np.uint8), panel)

                cv2.putText(
                    panel,
                    f"view {j}: red=boundary, blue=fused, orange=cross",
                    (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    panel,
                    f"view {j}: red=boundary, blue=fused, orange=cross",
                    (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (20, 20, 20),
                    1,
                    cv2.LINE_AA,
                )
                panels.append(panel)

            if panels:
                strip = panels[0]
                for p in panels[1:]:
                    gap = np.full((strip.shape[0], 10, 3), 16, dtype=np.uint8)
                    strip = np.hstack([strip, gap, p])
            else:
                strip = np.full((360, 720, 3), 16, dtype=np.uint8)

            max_atlas_h = max(260, int(0.9 * strip.shape[0]))
            atlas_scale = min(strip.shape[1] / max(1, overlay.shape[1]), max_atlas_h / max(1, overlay.shape[0]))
            atlas_w = max(1, int(round(overlay.shape[1] * atlas_scale)))
            atlas_h = max(1, int(round(overlay.shape[0] * atlas_scale)))
            atlas_vis = cv2.resize(overlay, (atlas_w, atlas_h), interpolation=cv2.INTER_LINEAR)
            if atlas_w < strip.shape[1]:
                pad_l = (strip.shape[1] - atlas_w) // 2
                pad_r = strip.shape[1] - atlas_w - pad_l
                atlas_vis = cv2.copyMakeBorder(
                    atlas_vis,
                    0,
                    0,
                    pad_l,
                    pad_r,
                    borderType=cv2.BORDER_CONSTANT,
                    value=(12, 12, 12),
                )

            sep = np.full((12, strip.shape[1], 3), 14, dtype=np.uint8)
            board = np.vstack([strip, sep, atlas_vis])
            cv2.putText(
                board,
                "combined-view debug: top=photos (red boundary/blue fill/orange cross), bottom=shared floor atlas",
                (14, strip.shape[0] + 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.46,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                board,
                "combined-view debug: top=photos (red boundary/blue fill/orange cross), bottom=shared floor atlas",
                (14, strip.shape[0] + 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.46,
                (20, 20, 20),
                1,
                cv2.LINE_AA,
            )

            overlay_path = out_dir / "reproject_stitched_floor_overlay.png"
            cv2.imwrite(str(overlay_path), board)
            artifacts["reproject_stitched_floor_overlay"] = str(overlay_path)

    return artifacts


def _save_scene_3d_artifacts(
    *,
    recon: Dust3RRecon,
    images: list[Path],
    scale_m_per_unit: float,
    floor_points_world: np.ndarray,
    floor_point_sources: np.ndarray,
    cam_centers: np.ndarray,
    out_dir: Path,
) -> dict[str, str]:
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}

    palette = np.array(
        [
            [230, 130, 40],
            [45, 110, 230],
            [70, 185, 80],
            [175, 90, 230],
            [55, 175, 205],
            [185, 160, 55],
            [220, 90, 120],
        ],
        dtype=np.uint8,
    )

    rng = np.random.default_rng(17)

    # 1) Full DUSt3R scene (all visible surfaces) with source-image colors.
    full_pts_list: list[np.ndarray] = []
    full_col_list: list[np.ndarray] = []
    n_views = min(len(images), len(recon.pts3d), len(recon.masks))
    for i in range(n_views):
        pts = np.asarray(recon.pts3d[i], dtype=np.float32)
        m = np.asarray(recon.masks[i], dtype=bool)
        if pts.ndim != 3 or pts.shape[2] != 3:
            continue
        if m.shape != pts.shape[:2]:
            continue

        finite = np.isfinite(pts).all(axis=2)
        keep = m & finite
        if int(keep.sum()) < 500:
            continue

        img = cv2.imread(str(images[i]), cv2.IMREAD_COLOR)
        if img is not None:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            if img.shape[:2] != keep.shape:
                img = cv2.resize(img, (keep.shape[1], keep.shape[0]), interpolation=cv2.INTER_LINEAR)
            cols = np.asarray(img[keep], dtype=np.uint8)
        else:
            cols = np.repeat(palette[i % len(palette)][None, :], int(keep.sum()), axis=0)

        pts_view = np.asarray(pts[keep], dtype=np.float32) * float(scale_m_per_unit)
        if pts_view.shape[0] > 120_000:
            idx = rng.choice(pts_view.shape[0], size=120_000, replace=False)
            pts_view = pts_view[idx]
            cols = cols[idx]

        full_pts_list.append(pts_view)
        full_col_list.append(cols)

    full_pts_out: np.ndarray | None = None
    if full_pts_list:
        full_pts = np.concatenate(full_pts_list, axis=0)
        full_cols = np.concatenate(full_col_list, axis=0)
        if full_pts.shape[0] > 300_000:
            idx = rng.choice(full_pts.shape[0], size=300_000, replace=False)
            full_pts = full_pts[idx]
            full_cols = full_cols[idx]
        full_path = out_dir / "scene_all_points.ply"
        _write_pointcloud_ply(full_path, full_pts, full_cols)
        artifacts["scene_all_points_ply"] = str(full_path)
        full_pts_out = full_pts

    # 2) Fused floor-only points (after scene-level plane inlier filtering).
    floor_pts = np.asarray(floor_points_world, dtype=np.float32)
    floor_src = np.asarray(floor_point_sources, dtype=np.int32)
    floor_pts_out: np.ndarray | None = None
    floor_src_out: np.ndarray | None = None
    if floor_pts.ndim == 2 and floor_pts.shape[1] == 3 and floor_pts.shape[0] > 0:
        floor_cols = palette[np.mod(floor_src, len(palette))]
        if floor_pts.shape[0] > 250_000:
            idx = rng.choice(floor_pts.shape[0], size=250_000, replace=False)
            floor_pts = floor_pts[idx]
            floor_cols = floor_cols[idx]
            floor_src = floor_src[idx]
        floor_path = out_dir / "scene_floor_points.ply"
        _write_pointcloud_ply(floor_path, floor_pts, floor_cols)
        artifacts["scene_floor_points_ply"] = str(floor_path)
        floor_pts_out = floor_pts
        floor_src_out = floor_src

    # 3) Camera centers.
    cams = np.asarray(cam_centers, dtype=np.float32)
    if cams.ndim == 2 and cams.shape[1] == 3 and cams.shape[0] > 0:
        cam_cols = np.repeat(np.array([[255, 40, 40]], dtype=np.uint8), cams.shape[0], axis=0)
        cam_path = out_dir / "scene_camera_centers.ply"
        _write_pointcloud_ply(cam_path, cams, cam_cols)
        artifacts["scene_camera_centers_ply"] = str(cam_path)
    else:
        cams = None

    viewer_path = _save_scene_html_viewer(
        out_path=out_dir / "scene_viewer.html",
        scene_points=full_pts_out,
        floor_points=floor_pts_out,
        floor_point_sources=floor_src_out,
        cam_centers=cams,
    )
    if viewer_path is not None:
        artifacts["scene_viewer_html"] = viewer_path

    return artifacts


def scene_multiview_floor(
    *,
    images: list[Path],
    floor_masks: list[np.ndarray],
    moge_results: list[MoGeResult],
    debug_dir: Path | None,
    impute_room_corners: bool,
    dust3r_niter: int,
    pose_method: str = "dust3r",
    moge_pose_matcher: str = "orb",
    moge_pose_allow_scale: bool = False,
) -> dict[str, object]:
    t0 = time.time()
    pose_key = (pose_method or "dust3r").strip().lower()
    pose_diag: PoseEstDiagnostics | None = None

    t1 = time.time()
    if pose_key in {"dust3r", "dust3r-scene"}:
        recon = infer_dust3r_scene(images, niter=dust3r_niter)
        t_recon = time.time() - t1

        t2 = time.time()
        metric_depths = [np.asarray(m.depth, dtype=np.float32) for m in moge_results]
        scale, scale_rel_std = estimate_scene_scale_m_per_unit(
            recon.depthmaps, metric_depths, recon.masks
        )
        t_scale = time.time() - t2

        cam2world_metric = recon.cam2world.copy()
        cam2world_metric[:, :3, 3] *= scale
    elif pose_key in {"moge-pose", "moge"}:
        pose_debug_dir = (debug_dir / "pose_est") if debug_dir is not None else None
        recon, pose_diag = infer_moge_pose_scene(
            images,
            moge_results,
            matcher=moge_pose_matcher,
            allow_scale=bool(moge_pose_allow_scale),
            debug_dir=pose_debug_dir,
        )
        t_recon = time.time() - t1
        scale = 1.0
        scale_rel_std = 0.0
        t_scale = 0.0
        cam2world_metric = recon.cam2world.copy()
    else:
        raise ValueError(f"Unknown pose method: {pose_method}")

    cam_centers = cam2world_metric[:, :3, 3]

    t3 = time.time()
    points_list: list[np.ndarray] = []
    source_list: list[np.ndarray] = []
    rng = np.random.default_rng(23)
    n_inputs = min(len(images), len(floor_masks), len(moge_results), cam2world_metric.shape[0])
    for i in range(n_inputs):
        moge = moge_results[i]
        floor_mask = np.asarray(floor_masks[i], dtype=bool)
        if floor_mask.shape != moge.depth.shape:
            import cv2

            floor_mask = cv2.resize(
                floor_mask.astype(np.uint8),
                (moge.depth.shape[1], moge.depth.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)

        valid = floor_mask & np.asarray(moge.mask, dtype=bool)
        if moge.normals is not None and moge.normals.any():
            valid = valid & (np.abs(np.asarray(moge.normals[..., 1], dtype=np.float32)) > 0.5)
        if int(valid.sum()) < 300:
            continue

        pts_cam = np.asarray(moge.points[valid], dtype=np.float64)
        finite = np.isfinite(pts_cam).all(axis=1)
        pts_cam = pts_cam[finite]
        if pts_cam.shape[0] < 300:
            continue

        # Drop very near/far outliers that destabilize world fusion.
        z = pts_cam[:, 2]
        keep_depth = (z > 0.10) & (z < 30.0)
        pts_cam = pts_cam[keep_depth]
        if pts_cam.shape[0] < 300:
            continue

        R = cam2world_metric[i, :3, :3].astype(np.float64)
        t = cam2world_metric[i, :3, 3].astype(np.float64)
        pts_world = pts_cam @ R.T + t[None, :]

        if pts_world.shape[0] > 300_000:
            idx = rng.choice(pts_world.shape[0], size=300_000, replace=False)
            pts_world = pts_world[idx]

        points_list.append(pts_world)
        source_list.append(np.full((pts_world.shape[0],), i, dtype=np.int32))

    if len(points_list) < 2:
        return {"ok": False, "reason": "insufficient_moge_floor_points_after_pose_fusion"}

    points = np.concatenate(points_list, axis=0)
    point_sources = np.concatenate(source_list, axis=0)
    if points.shape[0] < 1500:
        return {
            "ok": False,
            "reason": "too_few_world_points_after_filtering",
            "n_points": int(points.shape[0]),
        }

    gravity_up = _estimate_gravity_up(cam2world_metric)
    plane, inliers, floor_score = _find_floor_plane_in_scene(
        points,
        cam_centers,
        gravity_up,
        distance_thresh=0.05,
    )

    inlier_pts = points[inliers]
    if inlier_pts.shape[0] < 1200:
        return {
            "ok": False,
            "reason": "too_few_floor_inliers",
            "n_inliers": int(inlier_pts.shape[0]),
        }

    dists = np.abs(plane.signed_distance(inlier_pts))
    plane_residual = float(np.sqrt(np.mean(dists ** 2)))
    pts2d = _project_to_plane(inlier_pts, plane)
    alpha = _auto_alpha(pts2d)
    poly = _alpha_shape(pts2d, alpha)
    visible_m2 = float(poly.area)
    if not np.isfinite(visible_m2) or visible_m2 <= 0:
        return {
            "ok": False,
            "reason": "invalid_visible_area",
        }

    completed = poly
    completed_m2 = visible_m2
    rect_upper_m2 = visible_m2
    completion_mode = "visible-only"
    completion_info: dict[str, object] = {"strategy": "none"}
    if impute_room_corners:
        min_rect, completion_info = _completion_rectangle(poly, "manhattan-rect")
        rect_area = float(min_rect.area)
        rect_upper_m2 = max(rect_area, visible_m2)
        completed = min_rect
        completed_m2 = 0.5 * (visible_m2 + rect_upper_m2)
        completion_mode = "midpoint-between-bounds"

    poly_coords = np.asarray(poly.exterior.coords, dtype=np.float32) if hasattr(poly, "exterior") else None
    rect_coords = (
        np.asarray(completed.exterior.coords, dtype=np.float32)
        if hasattr(completed, "exterior")
        else None
    )
    cam2d = _project_to_plane(cam_centers, plane)
    artifacts: dict[str, str] = {}
    if debug_dir is not None:
        artifacts = _save_scene_floor_artifacts(
            points2d=pts2d.astype(np.float32),
            point_sources=point_sources[inliers],
            poly_coords=poly_coords,
            rect_coords=rect_coords if impute_room_corners else None,
            cam2d=cam2d.astype(np.float32),
            out_dir=debug_dir / "scene_pose_moge_floor",
        )
        artifacts.update(
            _save_scene_reprojection_artifacts(
                images=images,
                recon=recon,
                cam2world_metric=cam2world_metric,
                floor_points_world=inlier_pts.astype(np.float32),
                floor_point_sources=point_sources[inliers].astype(np.int32),
                floor_poly_coords_2d=poly_coords,
                floor_plane=plane,
                out_dir=debug_dir / "scene_pose_moge_floor",
            )
        )
        artifacts.update(
            _save_scene_3d_artifacts(
                recon=recon,
                images=images,
                scale_m_per_unit=scale,
                floor_points_world=inlier_pts.astype(np.float32),
                floor_point_sources=point_sources[inliers].astype(np.int32),
                cam_centers=cam_centers.astype(np.float32),
                out_dir=debug_dir / "scene_pose_moge_floor",
            )
        )
        if pose_diag is not None and pose_diag.artifacts:
            artifacts.update(pose_diag.artifacts)

    t_extract = time.time() - t3
    if pose_key in {"dust3r", "dust3r-scene"}:
        method_name = (
            "dust3r-pose+moge-floor+room-bounds" if impute_room_corners else "dust3r-pose+moge-floor-visible"
        )
    else:
        method_name = (
            "moge-pose+moge-floor+room-bounds" if impute_room_corners else "moge-pose+moge-floor-visible"
        )
    src_inliers: dict[str, int] = {}
    for i in range(n_inputs):
        src_inliers[str(i)] = int(np.sum(point_sources[inliers] == i))
    return {
        "ok": True,
        "method": method_name,
        "pose_method": str(pose_key),
        "pose_diagnostics": None if pose_diag is None else {
            "matcher": pose_diag.matcher,
            "allow_scale": pose_diag.allow_scale,
            "n_raw_matches": pose_diag.n_raw_matches,
            "n_3d_pairs": pose_diag.n_3d_pairs,
            "n_inliers": pose_diag.n_inliers,
            "rmse_m": pose_diag.rmse_m,
            "scale": pose_diag.scale,
            "ransac_thresh_m": pose_diag.ransac_thresh_m,
            "ransac_iters": pose_diag.ransac_iters,
            "artifacts": pose_diag.artifacts,
        },
        "completion_mode": completion_mode,
        "completion_geometry": completion_info,
        "visible_m2": visible_m2,
        "completed_m2": completed_m2,
        "rect_upper_m2": rect_upper_m2,
        "n_points": int(points.shape[0]),
        "n_floor_inliers": int(inlier_pts.shape[0]),
        "plane_residual_m": plane_residual,
        "alpha": float(alpha),
        "scale_m_per_unit": float(scale),
        "scale_rel_std": float(scale_rel_std),
        "floor_score": float(floor_score),
        "n_images_fused": int(len(points_list)),
        "source_inliers": src_inliers,
        "timings_s": {
            "reconstruct": float(t_recon),
            "scale": float(t_scale),
            "extract": float(t_extract),
            "total": float(time.time() - t0),
        },
        "artifacts": artifacts,
    }


# Legacy floor-patch stitching path was removed from runtime/CLI.


def _derive_estimate_from_multiview(
    *,
    multiview: dict[str, object],
    per_image_results: list[PerImageResult],
    impute_room_corners: bool,
) -> dict[str, float | str]:
    visible_area_m2 = float(multiview["visible_m2"])
    completed_area_m2 = float(multiview.get("rect_upper_m2", multiview["completed_m2"]))
    fused_m2 = float(multiview["completed_m2"]) if impute_room_corners else visible_area_m2
    method = str(multiview.get("method") or "multi-view")

    if impute_room_corners:
        lo_m2 = min(visible_area_m2, fused_m2)
        hi_m2 = max(completed_area_m2, fused_m2)
    else:
        image_areas = [r.area_m2 for r in per_image_results if r.area_m2 > 0]
        low_anchor = min([visible_area_m2] + image_areas) if image_areas else visible_area_m2
        high_anchor = max([visible_area_m2] + image_areas) if image_areas else visible_area_m2
        lo_m2 = low_anchor
        hi_m2 = high_anchor

    return {
        "fused_m2": float(fused_m2),
        "lo_m2": float(lo_m2),
        "hi_m2": float(hi_m2),
        "visible_area_m2": float(visible_area_m2),
        "completed_area_m2": float(completed_area_m2),
        "method": method,
    }


# ── 5. End-to-end pipeline ───────────────────────────────────────────────

def run_pipeline(
    images: list[Path],
    *,
    interactive: bool = False,
    debug_dir: Path | None = None,
    impute_room_corners: bool = False,
    multiview_method: str = "dust3r-scene",
    dust3r_niter: int = DUST3R_DEFAULT_ITERS,
    moge_pose_matcher: str = "orb",
    moge_pose_allow_scale: bool = False,
) -> EstimateResult:
    t0 = time.time()
    n_steps = 3
    per_image_results: list[PerImageResult] = []
    floor_masks: list[np.ndarray] = []
    moge_results: list[MoGeResult] = []

    # Step 1: Load models
    if interactive:
        _step(1, n_steps, "Loading models (SegFormer + MoGe-2)")
    t1 = time.time()
    load_segformer()
    load_moge()
    if interactive:
        _step_done(1, n_steps, "Loading models", time.time() - t1)

    # Step 2: Per-image inference
    if interactive:
        _step(2, n_steps, f"Processing {len(images)} images")
    t2 = time.time()

    debug_records: list[dict] = []
    for idx, img_path in enumerate(images):
        # Segment floor
        floor_mask = segment_floor(img_path)

        # MoGe-2 inference
        moge = infer_moge(img_path)

        # Resize floor mask to MoGe output resolution if needed
        if floor_mask.shape != moge.depth.shape:
            import cv2
            floor_mask = cv2.resize(
                floor_mask.astype(np.uint8),
                (moge.depth.shape[1], moge.depth.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)

        # Compute floor area
        area_m2, area_sqft, plane, residual, n_pts, area_debug = compute_floor_area_single_image(
            floor_mask,
            moge,
            return_debug=True,
        )
        floor_masks.append(np.asarray(floor_mask, dtype=bool))
        moge_results.append(moge)

        floor_frac = float(floor_mask.sum()) / max(1, floor_mask.size)
        depth_med = float(np.median(moge.depth[floor_mask])) if floor_mask.any() else 0.0

        per_image = PerImageResult(
            image_path=str(img_path),
            floor_mask_frac=floor_frac,
            n_floor_points_3d=n_pts,
            area_m2=area_m2,
            area_sqft=area_sqft,
            plane_residual=residual,
            depth_median_m=depth_med,
        )
        per_image_results.append(per_image)

        if debug_dir is not None:
            image_dir = debug_dir / "per_image" / f"{idx:02d}_{img_path.stem}"
            paths = _save_debug_artifacts(
                image_path=img_path,
                floor_mask=floor_mask,
                depth=moge.depth,
                area_debug=area_debug,
                out_dir=image_dir,
            )
            debug_records.append(
                {
                    "image": str(img_path),
                    "area_sqft": float(area_sqft),
                    "floor_mask_frac": float(floor_frac),
                    "n_floor_points_3d": int(n_pts),
                    "plane_residual_m": float(residual),
                    "depth_median_m": float(depth_med),
                    "alpha": None if area_debug is None or area_debug.alpha is None else float(area_debug.alpha),
                    "artifacts": paths,
                }
            )

    if interactive:
        _step_done(2, n_steps, f"Processing {len(images)} images", time.time() - t2)

    # Step 3: Fuse results
    if interactive:
        _step(3, n_steps, "Fusing results")
    t3 = time.time()

    method_key = (multiview_method or "dust3r-scene").strip().lower()
    if method_key not in {"dust3r-scene", "moge-pose", "single-image"}:
        raise ValueError(f"Unknown multiview method: {multiview_method}")

    candidate_details: dict[str, dict[str, object]] = {}
    selected_name: str | None = None
    selected_detail: dict[str, object] | None = None
    selected_derived: dict[str, float | str] | None = None

    if method_key == "dust3r-scene":
        rss_before = _max_rss_mb()
        start = time.time()
        method_debug_dir = (debug_dir / "multiview" / "dust3r-scene") if debug_dir is not None else None
        try:
            detail = scene_multiview_floor(
                images=images,
                floor_masks=floor_masks,
                moge_results=moge_results,
                debug_dir=method_debug_dir,
                impute_room_corners=impute_room_corners,
                dust3r_niter=dust3r_niter,
                pose_method="dust3r",
            )
        except Exception as ex:
            detail = {
                "ok": False,
                "reason": f"exception:{type(ex).__name__}",
                "error": str(ex),
            }
        runtime_s = float(time.time() - start)
        rss_after = _max_rss_mb()
        detail["runtime_s"] = runtime_s
        detail["peak_rss_mb"] = float(rss_after)
        detail["peak_rss_delta_mb"] = float(max(0.0, rss_after - rss_before))
        candidate_details["dust3r-scene"] = detail
        if bool(detail.get("ok")):
            selected_name = "dust3r-scene"
            selected_detail = detail
            selected_derived = _derive_estimate_from_multiview(
                multiview=detail,
                per_image_results=per_image_results,
                impute_room_corners=impute_room_corners,
            )
    elif method_key == "moge-pose":
        rss_before = _max_rss_mb()
        start = time.time()
        method_debug_dir = (debug_dir / "multiview" / "moge-pose") if debug_dir is not None else None
        try:
            detail = scene_multiview_floor(
                images=images,
                floor_masks=floor_masks,
                moge_results=moge_results,
                debug_dir=method_debug_dir,
                impute_room_corners=impute_room_corners,
                dust3r_niter=dust3r_niter,
                pose_method="moge-pose",
                moge_pose_matcher=moge_pose_matcher,
                moge_pose_allow_scale=bool(moge_pose_allow_scale),
            )
        except Exception as ex:
            detail = {
                "ok": False,
                "reason": f"exception:{type(ex).__name__}",
                "error": str(ex),
            }
        runtime_s = float(time.time() - start)
        rss_after = _max_rss_mb()
        detail["runtime_s"] = runtime_s
        detail["peak_rss_mb"] = float(rss_after)
        detail["peak_rss_delta_mb"] = float(max(0.0, rss_after - rss_before))
        candidate_details["moge-pose"] = detail
        if bool(detail.get("ok")):
            selected_name = "moge-pose"
            selected_detail = detail
            selected_derived = _derive_estimate_from_multiview(
                multiview=detail,
                per_image_results=per_image_results,
                impute_room_corners=impute_room_corners,
            )

    method = "per-image-fusion"
    visible_area_m2: float | None = None
    completed_area_m2: float | None = None
    if selected_detail is not None and bool(selected_detail.get("ok")) and selected_derived is not None:
        visible_area_m2 = float(selected_derived["visible_area_m2"])
        completed_area_m2 = float(selected_derived["completed_area_m2"])
        fused_m2 = float(selected_derived["fused_m2"])
        lo_m2 = float(selected_derived["lo_m2"])
        hi_m2 = float(selected_derived["hi_m2"])
        method = str(selected_derived["method"])
    else:
        fused_m2, lo_m2, hi_m2 = _fallback_fuse_floor_areas(per_image_results)

    sqft = fused_m2 * SQFT_PER_M2
    ci_lo = lo_m2 * SQFT_PER_M2
    ci_hi = hi_m2 * SQFT_PER_M2

    if interactive:
        _step_done(3, n_steps, "Fusing results", time.time() - t3)

    diagnostics = {}
    if debug_dir is not None:
        diagnostics["debug_dir"] = str(debug_dir)
        diagnostics["per_image_debug"] = debug_records
    diagnostics["multiview"] = {
        "requested": method_key,
        "selected": selected_name,
        "candidates": candidate_details,
    }

    return EstimateResult(
        sqft=sqft, area_m2=fused_m2,
        ci_lo=ci_lo, ci_hi=ci_hi,
        n_images=len(images),
        per_image=per_image_results,
        elapsed_s=time.time() - t0,
        method=method,
        visible_area_m2=visible_area_m2,
        completed_area_m2=completed_area_m2,
        diagnostics=diagnostics,
    )


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Estimate visible floor area from photos (v2b)")
    parser.add_argument("images_dir", nargs="?", type=Path, help="Folder of room photos")
    parser.add_argument("--max-images", type=int, default=6)
    parser.add_argument("--json", type=Path, help="Write results to JSON file")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="Optional directory to save debug artifacts (segmentation/depth/area visuals).",
    )
    parser.add_argument(
        "--room-impute",
        action="store_true",
        help="Enable rectangle-based corner completion after scene fusion.",
    )
    parser.add_argument(
        "--dust3r-iters",
        type=int,
        default=DUST3R_DEFAULT_ITERS,
        help="DUSt3R global alignment iterations (higher = slower, often marginally better alignment).",
    )
    parser.add_argument(
        "--multiview-method",
        choices=["dust3r-scene", "moge-pose", "single-image"],
        default="dust3r-scene",
        help="Multi-view fusion strategy. Use single-image to skip scene fusion.",
    )
    parser.add_argument(
        "--moge-pose-matcher",
        choices=["orb", "lightglue"],
        default="orb",
        help="Matcher used for MoGe-only pose estimation (moge-pose).",
    )
    parser.add_argument(
        "--moge-pose-allow-scale",
        action="store_true",
        help="Allow a similarity scale factor when fitting MoGe-only poses (moge-pose).",
    )
    args = parser.parse_args()

    interactive = args.interactive or args.images_dir is None

    if args.images_dir is None:
        folder = _interactive_select_folder()
    else:
        folder = args.images_dir.expanduser().resolve()
        if not folder.is_dir():
            print(f"Error: {folder} is not a directory", file=sys.stderr)
            sys.exit(1)

    images = _find_images(folder)
    if not images:
        print(f"Error: no images found in {folder}", file=sys.stderr)
        sys.exit(1)

    if interactive:
        images = _interactive_pick_images(images)
        if not images:
            print("No images selected.")
            sys.exit(0)
        print()

    images = images[:args.max_images]
    debug_dir = args.debug_dir
    if debug_dir is not None:
        debug_dir = debug_dir.expanduser().resolve()
    method_choice = args.multiview_method
    result = run_pipeline(
        images,
        interactive=interactive,
        debug_dir=debug_dir,
        impute_room_corners=args.room_impute,
        multiview_method=method_choice,
        dust3r_niter=max(20, args.dust3r_iters),
        moge_pose_matcher=str(args.moge_pose_matcher),
        moge_pose_allow_scale=bool(args.moge_pose_allow_scale),
    )

    if interactive:
        _display_result(result)
    else:
        print(f"{result.sqft:.0f} sqft  [{result.ci_lo:.0f}-{result.ci_hi:.0f}]  "
              f"({result.elapsed_s:.1f}s, {result.method})")
        if result.visible_area_m2 is not None and result.completed_area_m2 is not None:
            vis_sqft = result.visible_area_m2 * SQFT_PER_M2
            hi_sqft = result.completed_area_m2 * SQFT_PER_M2
            print(f"  scene_visible={vis_sqft:.0f} sqft, completion_upper={hi_sqft:.0f} sqft")
        for pr in result.per_image:
            name = Path(pr.image_path).name
            print(f"  {name}: {pr.area_sqft:.0f} sqft  "
                  f"(floor={pr.floor_mask_frac*100:.0f}%, "
                  f"pts={pr.n_floor_points_3d}, "
                  f"residual={pr.plane_residual:.3f}m)")

    # Save JSON
    json_path = args.json
    if json_path is None and interactive:
        resp = input("\n  Save results to JSON? [y/N] ").strip()
        if resp.lower() in ("y", "yes"):
            run_dir = Path(__file__).resolve().parent / "runs" / _run_stamp()
            run_dir.mkdir(parents=True, exist_ok=True)
            json_path = run_dir / "v2b_result.json"

    if json_path:
        out = {
            "variant": "v2b",
            "sqft": round(result.sqft, 1),
            "area_m2": round(result.area_m2, 2),
            "ci_90_lo": round(result.ci_lo, 1),
            "ci_90_hi": round(result.ci_hi, 1),
            "method": result.method,
            "visible_area_m2": None if result.visible_area_m2 is None else round(result.visible_area_m2, 2),
            "rect_upper_m2": None if result.completed_area_m2 is None else round(result.completed_area_m2, 2),
            "area_bounds_m2": (
                None
                if result.visible_area_m2 is None or result.completed_area_m2 is None
                else {
                    "lo": round(min(result.visible_area_m2, result.completed_area_m2), 2),
                    "hi": round(max(result.visible_area_m2, result.completed_area_m2), 2),
                }
            ),
            "n_images": result.n_images,
            "elapsed_s": round(result.elapsed_s, 1),
            "per_image": [
                {
                    "image": pr.image_path,
                    "area_sqft": round(pr.area_sqft, 1),
                    "area_m2": round(pr.area_m2, 2),
                    "floor_mask_frac": round(pr.floor_mask_frac, 3),
                    "n_floor_points_3d": pr.n_floor_points_3d,
                    "plane_residual_m": round(pr.plane_residual, 4),
                    "depth_median_m": round(pr.depth_median_m, 2),
                }
                for pr in result.per_image
            ],
            "diagnostics": result.diagnostics,
        }
        Path(json_path).write_text(json.dumps(out, indent=2) + "\n")
        print(f"\n  Saved to {json_path}" if interactive else f"Saved: {json_path}")


if __name__ == "__main__":
    main()
