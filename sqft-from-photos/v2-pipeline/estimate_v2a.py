#!/usr/bin/env python3
"""
sqft-from-photos v2 — Estimate room area from a few photos.

Uses DUSt3R (learned multi-view reconstruction) + Depth-Anything-V2 (metric depth)
to build a metric 3D point cloud, find the floor plane, and compute area.

Usage:
    python 0_estimate_sqft.py                     # interactive TUI
    python 0_estimate_sqft.py /path/to/photos     # batch mode
    python 0_estimate_sqft.py photos/ --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ── Paths to cached models ────────────────────────────────────────────────
VOLUME_ROOT = Path.home() / ".cache" / "cv_pipeline"
VENDOR_DIR = VOLUME_ROOT / "models" / "vendor"
CHECKPOINTS_DIR = VOLUME_ROOT / "models" / "checkpoints"

DUST3R_VENDOR = VENDOR_DIR / "dust3r"
DA2_VENDOR = VENDOR_DIR / "depth-anything-v2" / "metric_depth"
DUST3R_CKPT = CHECKPOINTS_DIR / "dust3r" / "DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth"
DA2_CKPT = CHECKPOINTS_DIR / "depth_anything_v2_metric_hypersim_vitl.pth"

IMAGE_SIZE = 512
NITER = 300
MAX_DEPTH_M = 20.0
SQFT_PER_M2 = 10.763910416709722
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


# ── Data classes ──────────────────────────────────────────────────────────
@dataclass
class ReconResult:
    depthmaps: list[np.ndarray]       # (H,W) float32 in DUSt3R units
    pts3d: list[np.ndarray]           # (H,W,3) float32
    masks: list[np.ndarray]           # (H,W) bool
    cam2world: np.ndarray             # (N,4,4)
    intrinsics: np.ndarray            # (N,3,3)


@dataclass
class Plane:
    normal: np.ndarray   # (3,) unit
    d: float             # plane eq: normal . x + d = 0

    def signed_distance(self, pts: np.ndarray) -> np.ndarray:
        return pts @ self.normal + self.d


@dataclass
class EstimateResult:
    sqft: float
    area_m2: float
    ci_lo: float
    ci_hi: float
    scale: float
    scale_std: float
    n_images: int
    floor_inliers: int
    floor_score: float
    elapsed_s: float
    diagnostics: dict = field(default_factory=dict)


# ── TUI helpers ───────────────────────────────────────────────────────────
def _box(lines: list[str], width: int = 42) -> str:
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
    imgs = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")
    )
    return imgs


def _interactive_select_folder() -> Path:
    print("\n  sqft-from-photos v2")
    print("  " + "\u2500" * 30)
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


def _interactive_confirm_images(images: list[Path]) -> list[Path]:
    print(f"\n  Found {len(images)} images:")
    for i, img in enumerate(images):
        print(f"    {i+1}. {img.name}")
    resp = input("\n  Use all? [Y/n] or enter numbers to exclude (e.g. '3 5'): ").strip()
    if not resp or resp.lower() in ("y", "yes"):
        return images
    try:
        exclude = {int(x) for x in resp.split()}
        return [img for i, img in enumerate(images) if (i + 1) not in exclude]
    except ValueError:
        return images


def _display_result(result: EstimateResult):
    quality = "good" if result.scale_std < 0.15 else "fair" if result.scale_std < 0.30 else "poor"
    lines = [
        f"Estimated area:  {result.sqft:.0f} sqft",
        f"90% CI:          [{result.ci_lo:.0f} \u2013 {result.ci_hi:.0f}] sqft",
        f"Scale quality:   {quality} (std={result.scale_std:.2f})",
        f"Floor inliers:   {result.floor_inliers:,} points",
        f"Images used:     {result.n_images}",
        f"Total time:      {result.elapsed_s:.1f}s",
    ]
    print("\n" + _box(lines))


# ── 1. DUSt3R reconstruction ─────────────────────────────────────────────
def reconstruct(images: list[Path], *, max_images: int = 6) -> ReconResult:
    if str(DUST3R_VENDOR) not in sys.path:
        sys.path.insert(0, str(DUST3R_VENDOR))

    import torch
    if hasattr(torch.serialization, "add_safe_globals"):
        torch.serialization.add_safe_globals([argparse.Namespace])

    from dust3r.cloud_opt import GlobalAlignerMode, global_aligner
    from dust3r.image_pairs import make_pairs
    from dust3r.inference import inference
    from dust3r.model import AsymmetricCroCo3DStereo
    from dust3r.utils.image import load_images

    images = images[:max_images]
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

    model = AsymmetricCroCo3DStereo.from_pretrained(str(DUST3R_CKPT)).to(device).eval()
    imgs = load_images([str(p) for p in images], size=IMAGE_SIZE)
    pairs = make_pairs(imgs, scene_graph="complete", prefilter=None, symmetrize=True)
    output = inference(pairs, model, device, batch_size=1, verbose=False)

    scene = global_aligner(output, device=device, mode=GlobalAlignerMode.PointCloudOptimizer)
    scene.compute_global_alignment(init="mst", niter=NITER, schedule="cosine", lr=0.01)

    depthmaps = [d.detach().float().cpu().numpy() for d in scene.get_depthmaps()]
    pts3d = [p.detach().float().cpu().numpy() for p in scene.get_pts3d()]
    masks = [m.detach().cpu().numpy().astype(bool) for m in scene.get_masks()]
    cam2world = scene.get_im_poses().detach().float().cpu().numpy()
    intrinsics = scene.get_intrinsics().detach().float().cpu().numpy()

    # Free GPU memory
    del model, scene, output, pairs, imgs
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return ReconResult(depthmaps=depthmaps, pts3d=pts3d, masks=masks,
                       cam2world=cam2world, intrinsics=intrinsics)


# ── 2. Metric depth inference ─────────────────────────────────────────────
def infer_metric_depth(images: list[Path]) -> list[np.ndarray]:
    if str(DA2_VENDOR) not in sys.path:
        sys.path.insert(0, str(DA2_VENDOR))

    import cv2
    import torch
    from depth_anything_v2.dpt import DepthAnythingV2

    cfg = {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]}
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

    model = DepthAnythingV2(**cfg, max_depth=MAX_DEPTH_M)
    state = torch.load(str(DA2_CKPT), map_location="cpu")
    model.load_state_dict(state)
    model = model.to(device).eval()

    depths = []
    for img_path in images:
        raw = cv2.imread(str(img_path))
        if raw is None:
            raise RuntimeError(f"Failed to read image: {img_path}")
        depth = model.infer_image(raw, 518)
        depths.append(np.asarray(depth, dtype=np.float32))

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return depths


# ── 3. Scale alignment ────────────────────────────────────────────────────
# Adapted from cv_pipeline/sfm/scale_depthmaps.py

def _robust_mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    return 1.4826 * mad


def estimate_scale(
    dust3r_depths: list[np.ndarray],
    metric_depths: list[np.ndarray],
    masks: list[np.ndarray],
    *,
    max_depth_m: float = MAX_DEPTH_M,
    min_pixels: int = 10_000,
) -> tuple[float, float]:
    """Returns (scale_m_per_unit, scale_rel_std)."""
    import cv2

    per_image = []
    weights = []

    for du, dm, m in zip(dust3r_depths, metric_depths, masks):
        # Resize metric depth to match DUSt3R resolution
        if dm.shape != du.shape:
            dm = cv2.resize(dm, (du.shape[1], du.shape[0]), interpolation=cv2.INTER_LINEAR)

        du = du.astype(np.float64)
        dm = dm.astype(np.float64)
        valid = np.isfinite(du) & np.isfinite(dm) & (du > 1e-6) & (dm > 1e-6) & (dm < max_depth_m) & m
        if int(valid.sum()) < min_pixels:
            continue

        ratios = dm[valid] / du[valid]
        med = float(np.median(ratios))
        mad = _robust_mad(ratios)
        if not np.isfinite(med) or med <= 0:
            continue

        if mad > 0:
            z = np.abs((ratios - med) / mad)
            inlier = z < 3.5
        else:
            inlier = np.ones_like(ratios, dtype=bool)
        if int(inlier.sum()) < min_pixels:
            continue

        s_i = float(np.median(ratios[inlier]))
        per_image.append(s_i)
        weights.append(float(inlier.sum()))

    if not per_image:
        raise RuntimeError("Scale estimation failed: no images produced enough valid depth pairs.")

    values = np.asarray(per_image, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    order = np.argsort(values)
    values, w = values[order], w[order]
    cum = np.cumsum(w)
    idx = int(np.searchsorted(cum, 0.5 * cum[-1]))
    scale = float(values[min(idx, len(values) - 1)])
    rel_std = float(_robust_mad(values) / max(scale, 1e-9))

    return scale, rel_std


# ── 4. Floor extraction ──────────────────────────────────────────────────
# Adapted from cv_pipeline/geometry/planes.py + footprint.py

def _estimate_gravity_up(cam2world: np.ndarray) -> np.ndarray:
    """Approximate gravity 'up' from camera poses (cam2world 4x4 matrices)."""
    up_cam = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    ups = []
    for i in range(cam2world.shape[0]):
        R_c2w = cam2world[i, :3, :3]
        ups.append(R_c2w @ up_cam)
    v = np.mean(np.stack(ups), axis=0)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])


def _ransac_plane(points: np.ndarray, *, num_iter: int = 800,
                  distance_thresh: float = 0.05, rng: np.random.Generator) -> tuple[Plane, np.ndarray]:
    best_inliers = None
    best_plane = None
    n_points = points.shape[0]

    for _ in range(num_iter):
        idx = rng.choice(n_points, size=3, replace=False)
        p0, p1, p2 = points[idx]
        n = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n = n / norm
        d = -float(n @ p0)
        plane = Plane(normal=n, d=d)
        dist = np.abs(plane.signed_distance(points))
        inliers = dist < distance_thresh
        if best_inliers is None or int(inliers.sum()) > int(best_inliers.sum()):
            best_inliers = inliers
            best_plane = plane

    if best_inliers is None or best_plane is None:
        raise RuntimeError("RANSAC failed to find a plane")
    return best_plane, best_inliers


def _find_floor_plane(points: np.ndarray, cam_centers: np.ndarray,
                      gravity_up: np.ndarray, *, distance_thresh: float = 0.05,
                      rng: np.random.Generator) -> tuple[Plane, np.ndarray, float]:
    if points.shape[0] > 200_000:
        idx = rng.choice(points.shape[0], size=200_000, replace=False)
        pts = points[idx]
    else:
        pts = points

    best_score, best, best_inliers = -1.0, None, None

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
            best_score, best, best_inliers = score, plane, inliers

        pts = pts[~inliers]
        if pts.shape[0] < 10_000:
            break

    if best is None or best_inliers is None:
        raise RuntimeError("Failed to find floor plane")

    inliers_full = np.abs(best.signed_distance(points)) < distance_thresh
    return best, inliers_full, best_score


def _plane_basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0.0, 0.0])
    if abs(float(a @ n)) > 0.9:
        a = np.array([0.0, 1.0, 0.0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    v /= np.linalg.norm(v)
    return u, v


def _project_to_plane(points: np.ndarray, plane: Plane) -> np.ndarray:
    u, v = _plane_basis(plane.normal)
    p0 = -plane.d * plane.normal
    q = points - p0[None, :]
    return np.stack([q @ u, q @ v], axis=1)


def _alpha_shape(pts2d: np.ndarray, alpha: float):
    from scipy.spatial import Delaunay, cKDTree
    from shapely.geometry import LineString, MultiPoint, Polygon
    from shapely.ops import polygonize, unary_union

    if pts2d.shape[0] < 4:
        return MultiPoint(pts2d).convex_hull

    tri = Delaunay(pts2d)
    triangles = pts2d[tri.simplices]

    def circumradius(t):
        a = np.linalg.norm(t[0] - t[1])
        b = np.linalg.norm(t[1] - t[2])
        c = np.linalg.norm(t[2] - t[0])
        s = 0.5 * (a + b + c)
        area2 = max(s * (s - a) * (s - b) * (s - c), 0.0)
        if area2 <= 1e-18:
            return float("inf")
        return (a * b * c) / (4.0 * np.sqrt(area2))

    edges: dict[tuple[int, int], int] = {}
    for simplex, t in zip(tri.simplices, triangles):
        r = circumradius(t)
        if r > alpha:
            continue
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edge = (min(simplex[i], simplex[j]), max(simplex[i], simplex[j]))
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


def compute_floor_area(points: np.ndarray, cam2world: np.ndarray,
                       distance_thresh: float = 0.06) -> tuple[float, float, int, float]:
    """Returns (sqft, area_m2, n_floor_inliers, floor_score)."""
    rng = np.random.default_rng(42)
    gravity_up = _estimate_gravity_up(cam2world)
    cam_centers = cam2world[:, :3, 3]

    plane, inliers, score = _find_floor_plane(
        points, cam_centers, gravity_up, distance_thresh=distance_thresh, rng=rng
    )

    floor_pts = points[inliers]
    pts2d = _project_to_plane(floor_pts, plane)
    alpha = _auto_alpha(pts2d)
    poly = _alpha_shape(pts2d, alpha)
    area_m2 = float(poly.area)
    return area_m2 * SQFT_PER_M2, area_m2, int(inliers.sum()), score


# ── 5. End-to-end pipeline ───────────────────────────────────────────────
def run_pipeline(images: list[Path], *, max_images: int = 6,
                 interactive: bool = False) -> EstimateResult:
    t0 = time.time()
    total_steps = 4
    images = images[:max_images]

    # Step 1: DUSt3R reconstruction
    if interactive:
        _step(1, total_steps, "DUSt3R reconstruction")
    t1 = time.time()
    recon = reconstruct(images, max_images=max_images)
    if interactive:
        _step_done(1, total_steps, "DUSt3R reconstruction", time.time() - t1)

    # Step 2: Metric depth
    if interactive:
        _step(2, total_steps, "Metric depth estimation")
    t2 = time.time()
    metric_depths = infer_metric_depth(images)
    if interactive:
        _step_done(2, total_steps, "Metric depth estimation", time.time() - t2)

    # Step 3: Scale alignment
    if interactive:
        _step(3, total_steps, "Scale alignment")
    t3 = time.time()
    scale, scale_std = estimate_scale(recon.depthmaps, metric_depths, recon.masks)
    if interactive:
        _step_done(3, total_steps, "Scale alignment", time.time() - t3)

    # Step 4: Build metric cloud + floor extraction
    if interactive:
        _step(4, total_steps, "Floor extraction")
    t4 = time.time()

    pts_list = []
    for pts_i, mask_i in zip(recon.pts3d, recon.masks):
        pts = pts_i[mask_i] * scale
        if pts.shape[0] > 250_000:
            idx = np.random.default_rng(0).choice(pts.shape[0], 250_000, replace=False)
            pts = pts[idx]
        pts_list.append(pts)
    points = np.concatenate(pts_list)

    cam2world_metric = recon.cam2world.copy()
    cam2world_metric[:, :3, 3] *= scale

    sqft, area_m2, floor_inliers, floor_score = compute_floor_area(points, cam2world_metric)

    if interactive:
        _step_done(4, total_steps, "Floor extraction", time.time() - t4)

    # Confidence interval (log-normal)
    sigma = max(1e-6, 2.0 * scale_std) * 1.25
    ci_lo = sqft * np.exp(-1.645 * sigma)
    ci_hi = sqft * np.exp(1.645 * sigma)

    return EstimateResult(
        sqft=sqft, area_m2=area_m2,
        ci_lo=ci_lo, ci_hi=ci_hi,
        scale=scale, scale_std=scale_std,
        n_images=len(images),
        floor_inliers=floor_inliers,
        floor_score=floor_score,
        elapsed_s=time.time() - t0,
    )


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Estimate room area from photos")
    parser.add_argument("images_dir", nargs="?", type=Path, help="Folder of room photos")
    parser.add_argument("--max-images", type=int, default=6, help="Max images to use (default: 6)")
    parser.add_argument("--json", type=Path, help="Write results to JSON file")
    parser.add_argument("--interactive", action="store_true", help="Force interactive mode")
    args = parser.parse_args()

    interactive = args.interactive or args.images_dir is None

    # Get images folder
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
        images = _interactive_confirm_images(images)
        if not images:
            print("No images selected.")
            sys.exit(0)
        print()

    # Truncate to max images before pipeline (so JSON reflects actual images used)
    images = images[:args.max_images]

    # Run pipeline
    result = run_pipeline(images, max_images=args.max_images, interactive=interactive)

    # Display results
    if interactive:
        _display_result(result)
    else:
        print(f"{result.sqft:.0f} sqft  [{result.ci_lo:.0f}-{result.ci_hi:.0f}]  "
              f"(scale_std={result.scale_std:.3f}, floor_inliers={result.floor_inliers}, "
              f"{result.elapsed_s:.1f}s)")

    # Save JSON
    json_path = args.json
    if json_path is None and interactive:
        resp = input("\n  Save results to JSON? [y/N] ").strip()
        if resp.lower() in ("y", "yes"):
            json_path = folder / "sqft_estimate.json"

    if json_path:
        out = {
            "sqft": round(result.sqft, 1),
            "area_m2": round(result.area_m2, 2),
            "ci_90_lo": round(result.ci_lo, 1),
            "ci_90_hi": round(result.ci_hi, 1),
            "scale_m_per_unit": round(result.scale, 6),
            "scale_rel_std": round(result.scale_std, 4),
            "n_images": result.n_images,
            "floor_inliers": result.floor_inliers,
            "floor_score": round(result.floor_score, 4),
            "elapsed_s": round(result.elapsed_s, 1),
            "images": [str(p) for p in images],
        }
        Path(json_path).write_text(json.dumps(out, indent=2) + "\n")
        print(f"\n  Saved to {json_path}" if interactive else f"Saved: {json_path}")


if __name__ == "__main__":
    main()
