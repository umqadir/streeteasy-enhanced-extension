#!/usr/bin/env python3
"""
v2b: Visible floor area estimation from room photos.

Pipeline: SegFormer (floor segmentation) + MoGe-2 (metric depth + intrinsics + normals)
  1. Segment floor pixels (SegFormer-B5 on ADE20K)
  2. Predict metric 3D points + normals (MoGe-2)
  3. Select floor points = segmentation mask AND upward-facing normals
  4. Fit floor plane (RANSAC), enforce planarity
  5. Project floor points onto plane, compute area (alpha shape)
  6. Multi-view: fuse floor areas across images for better coverage

Usage:
    uv run python estimate_v2b.py                     # interactive TUI
    uv run python estimate_v2b.py /path/to/photos     # batch mode
    uv run python estimate_v2b.py photos/ --json out.json --single  # single-image mode
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
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

SQFT_PER_M2 = 10.763910416709722
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

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
    diagnostics: dict = field(default_factory=dict)


@dataclass
class AreaDebug:
    pts2d: np.ndarray | None = None
    poly_coords: np.ndarray | None = None
    alpha: float | None = None
    n_inliers: int = 0


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
    lines = [
        f"Visible floor area:  {result.sqft:.0f} sqft",
        f"90% CI:              [{result.ci_lo:.0f} \u2013 {result.ci_hi:.0f}] sqft",
        f"Images used:         {result.n_images}",
        f"Total time:          {result.elapsed_s:.1f}s",
        "",
    ]
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


# ── 4. Multi-view fusion ─────────────────────────────────────────────────

def fuse_floor_areas(per_image_results: list[PerImageResult]) -> tuple[float, float, float]:
    """
    Combine per-image floor area estimates.

    For now: weighted average by number of floor points (more points = more floor visible).
    Returns: (fused_m2, ci_lo_m2, ci_hi_m2)
    """
    valid = [r for r in per_image_results if r.area_m2 > 0 and r.plane_residual < 0.10]
    if not valid:
        # Fall back to all results
        valid = [r for r in per_image_results if r.area_m2 > 0]
    if not valid:
        return 0.0, 0.0, 0.0

    # Take the maximum area (each image sees a portion; the most visible gives the best estimate)
    # But also report the range
    areas = np.array([r.area_m2 for r in valid])
    weights = np.array([r.n_floor_points_3d for r in valid], dtype=float)

    # Best estimate: weighted average biased toward larger areas
    # Rationale: each image sees a subset of the floor. The image seeing the most
    # gives the closest-to-true-visible-area. But averaging helps with noise.
    # Use the 75th percentile as the point estimate (between max and median)
    if len(areas) >= 3:
        fused = float(np.percentile(areas, 75))
    else:
        fused = float(np.max(areas))

    # Uncertainty from spread
    lo = float(np.min(areas)) * 0.9
    hi = float(np.max(areas)) * 1.1
    return fused, lo, hi


# ── 5. End-to-end pipeline ───────────────────────────────────────────────

def run_pipeline(images: list[Path], *, interactive: bool = False, debug_dir: Path | None = None) -> EstimateResult:
    t0 = time.time()
    n_steps = 3
    per_image_results = []

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
            return_debug=debug_dir is not None,
        )

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

    fused_m2, lo_m2, hi_m2 = fuse_floor_areas(per_image_results)
    sqft = fused_m2 * SQFT_PER_M2
    ci_lo = lo_m2 * SQFT_PER_M2
    ci_hi = hi_m2 * SQFT_PER_M2

    if interactive:
        _step_done(3, n_steps, "Fusing results", time.time() - t3)

    diagnostics = {}
    if debug_dir is not None:
        diagnostics["debug_dir"] = str(debug_dir)
        diagnostics["per_image_debug"] = debug_records
    return EstimateResult(
        sqft=sqft, area_m2=fused_m2,
        ci_lo=ci_lo, ci_hi=ci_hi,
        n_images=len(images),
        per_image=per_image_results,
        elapsed_s=time.time() - t0,
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
    result = run_pipeline(images, interactive=interactive, debug_dir=debug_dir)

    if interactive:
        _display_result(result)
    else:
        print(f"{result.sqft:.0f} sqft  [{result.ci_lo:.0f}-{result.ci_hi:.0f}]  "
              f"({result.elapsed_s:.1f}s)")
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
            json_path = folder / "sqft_v2b.json"

    if json_path:
        out = {
            "variant": "v2b",
            "sqft": round(result.sqft, 1),
            "area_m2": round(result.area_m2, 2),
            "ci_90_lo": round(result.ci_lo, 1),
            "ci_90_hi": round(result.ci_hi, 1),
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
