#!/usr/bin/env python3
"""
v3_benchmakrs: benchmark harness for v2b single-image + multi-view floor-area pipelines.

Design constraints:
1) Baseline runs are executed through estimate_v2b.run_pipeline *without* modifications.
2) Single-image and multi-view tracks are benchmarked separately with different swap options.
3) Outputs are organized into tabular summaries + per-run visual evidence folders.

Usage examples:
    uv run python v3_benchmakrs.py --images-dir ../sample-collection/clean_set_export/photos/listing_001 --known-room-sqft 266
    uv run python v3_benchmakrs.py --cases-json ./bench_cases.json
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

import estimate_v2b as v2b

REPO_ROOT = Path(__file__).resolve().parents[1]
CV_PIPELINE_SRC = REPO_ROOT / "cv-pipeline" / "src"
CV_PIPELINE_DOWNLOAD_SCRIPT = REPO_ROOT / "cv-pipeline" / "scripts" / "download_models.py"
if str(CV_PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(CV_PIPELINE_SRC))

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def _list_images(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")
    )


@dataclass(frozen=True)
class BenchCase:
    name: str
    images: list[Path]
    known_room_sqft: float | None = None


@dataclass(frozen=True)
class RunSpec:
    track: str  # single | multiview
    category: str
    name: str
    segmentation: str = "segformer_b5_ade20k"
    depth: str = "moge2"
    reconstruction: str = "dust3r"
    scale_anchor: str = "moge"
    plane_fit: str = "ransac"
    boundary: str = "alpha"
    floor_source: str = "moge"  # moge | recon
    baseline_exact_v2b: bool = False


@dataclass
class RunRecord:
    case: str
    track: str
    category: str
    config: str
    status: str
    method: str | None
    area_sqft: float | None
    ci_lo_sqft: float | None
    ci_hi_sqft: float | None
    known_room_sqft: float | None
    abs_error_sqft: float | None
    pct_error: float | None
    runtime_s: float | None
    peak_rss_delta_mb: float | None
    peak_vram_mb: float | None
    reprojection_iou_mean: float | None
    debug_dir: str
    key_visual: str | None
    error: str | None = None


class SegmentationBackends:
    def __init__(self) -> None:
        self._mask2former = None
        self._oneformer = None
        self._sam2 = None

    @staticmethod
    def _find_floor_ids(id2label: dict) -> set[int]:
        floor_ids: set[int] = set()
        for k, v in id2label.items():
            try:
                idx = int(k)
            except Exception:
                try:
                    idx = int(float(k))
                except Exception:
                    continue
            label = str(v).lower()
            if "floor" in label or "rug" in label or "carpet" in label:
                floor_ids.add(idx)
        # ADE20K canonical fallback
        floor_ids.update({3, 28})
        return floor_ids

    def _load_mask2former(self):
        if self._mask2former is not None:
            return
        import torch
        from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation

        model_name = "facebook/mask2former-swin-large-ade-semantic"
        processor = AutoImageProcessor.from_pretrained(model_name)
        model = Mask2FormerForUniversalSegmentation.from_pretrained(model_name)
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self._mask2former = (processor, model.to(device).eval(), device)

    def _load_oneformer(self):
        if self._oneformer is not None:
            return
        import torch
        from transformers import OneFormerForUniversalSegmentation, OneFormerProcessor

        model_name = "shi-labs/oneformer_ade20k_swin_large"
        processor = OneFormerProcessor.from_pretrained(model_name)
        model = OneFormerForUniversalSegmentation.from_pretrained(model_name)
        # OneFormer frequently hits float64/MPS kernel gaps on Apple Silicon; keep CPU fallback stable.
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._oneformer = (processor, model.to(device).eval(), device)

    def _load_sam2(self):
        if self._sam2 is not None:
            return
        import torch
        from transformers import AutoModelForMaskGeneration, AutoProcessor

        # Lightweight-ish SAM2 variant from HF. This is a coarse prompt-less fallback.
        model_name = "facebook/sam2-hiera-large"
        processor = AutoProcessor.from_pretrained(model_name)
        model = AutoModelForMaskGeneration.from_pretrained(model_name)
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self._sam2 = (processor, model.to(device).eval(), device)

    def segment(self, name: str, image_path: Path) -> np.ndarray:
        if name == "segformer_b5_ade20k":
            return v2b.segment_floor(image_path)

        if name == "mask2former_ade20k":
            from PIL import Image
            import torch

            self._load_mask2former()
            processor, model, device = self._mask2former
            image = Image.open(image_path).convert("RGB")
            w, h = image.size
            inputs = processor(images=image, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = model(**inputs)
            seg = processor.post_process_semantic_segmentation(outputs, target_sizes=[(h, w)])[0]
            seg_np = np.asarray(seg.detach().cpu().numpy(), dtype=np.int32)
            floor_ids = self._find_floor_ids(model.config.id2label)
            return np.isin(seg_np, list(floor_ids))

        if name == "oneformer_ade20k":
            from PIL import Image
            import torch

            self._load_oneformer()
            processor, model, device = self._oneformer
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
            floor_ids = self._find_floor_ids(model.config.id2label)
            return np.isin(seg_np, list(floor_ids))

        if name == "sam2_prompt_floor":
            # SAM2 here is prompt-less mask generation; we select the largest low-half mask as a floor prior.
            from PIL import Image
            import torch

            self._load_sam2()
            processor, model, device = self._sam2
            image = Image.open(image_path).convert("RGB")
            w, h = image.size
            try:
                inputs = processor(images=image, return_tensors="pt")
                inputs = {k: v.to(device) for k, v in inputs.items()}
                with torch.no_grad():
                    outputs = model(**inputs)
                masks = processor.post_process_masks(
                    masks=outputs.pred_masks,
                    original_sizes=[(h, w)],
                    reshaped_input_sizes=[(h, w)],
                )[0]
            except Exception as ex:
                raise RuntimeError(f"SAM2 inference failed in this environment: {ex}") from ex
            if len(masks) == 0:
                raise RuntimeError("SAM2 produced no masks.")
            yy = np.linspace(0.0, 1.0, h, endpoint=False)[:, None]
            best_score = -1.0
            best_mask = None
            for m in masks:
                m_np = np.asarray(m.detach().cpu().numpy() > 0, dtype=bool)
                area = float(m_np.mean())
                if area < 0.01:
                    continue
                low_half = float((m_np & (yy > 0.45)).sum()) / max(1.0, float(m_np.sum()))
                score = 0.7 * low_half + 0.3 * area
                if score > best_score:
                    best_score = score
                    best_mask = m_np
            if best_mask is None:
                raise RuntimeError("SAM2 masks were all too small.")
            return best_mask

        raise ValueError(f"Unknown segmentation backend: {name}")


def _default_intrinsics(width: int, height: int, fov_x_deg: float = 70.0) -> np.ndarray:
    fx = 0.5 * float(width) / math.tan(math.radians(fov_x_deg) * 0.5)
    fy = fx
    cx = 0.5 * float(width)
    cy = 0.5 * float(height)
    k = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float32)
    return k


def _depth_to_points(depth: np.ndarray, intrinsics: np.ndarray) -> np.ndarray:
    h, w = depth.shape[:2]
    fx = float(intrinsics[0, 0])
    fy = float(intrinsics[1, 1])
    cx = float(intrinsics[0, 2])
    cy = float(intrinsics[1, 2])
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
    z = np.asarray(depth, dtype=np.float32)
    x = (xx - cx) / max(1e-8, fx) * z
    y = (yy - cy) / max(1e-8, fy) * z
    pts = np.stack([x, y, z], axis=-1).astype(np.float32)
    return pts


def _points_to_normals(points: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(points, dtype=np.float32)
    dx = np.zeros_like(points, dtype=np.float32)
    dy = np.zeros_like(points, dtype=np.float32)
    dx[:, 1:-1] = points[:, 2:] - points[:, :-2]
    dy[1:-1, :] = points[2:, :] - points[:-2, :]
    n = np.cross(dx, dy)
    n_norm = np.linalg.norm(n, axis=2, keepdims=True)
    good = (n_norm[..., 0] > 1e-8) & valid_mask
    normals[good] = (n[good] / n_norm[good]).astype(np.float32)
    return normals


class DepthBackends:
    def __init__(self) -> None:
        self._unidepth_v2 = None
        self._depth_anything = None
        self._zoe = None
        self._metric3d = None

    def infer(self, name: str, image_path: Path) -> v2b.MoGeResult:
        if name == "moge2":
            return v2b.infer_moge(image_path)
        if name == "unidepth_v2":
            return self._infer_unidepth_v2(image_path)
        if name == "depth_anything_v2_metric":
            return self._infer_depth_anything(image_path)
        if name == "zoedepth_metric":
            return self._infer_zoedepth(image_path)
        if name == "metric3d_v2":
            return self._infer_metric3d(image_path)
        raise ValueError(f"Unknown depth backend: {name}")

    def metric_depth_only(self, name: str, image_path: Path) -> np.ndarray:
        out = self.infer(name, image_path)
        return np.asarray(out.depth, dtype=np.float32)

    def _infer_unidepth_v2(self, image_path: Path) -> v2b.MoGeResult:
        if self._unidepth_v2 is None:
            vendor = v2b.VENDOR_DIR / "unidepth"
            if str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            import torch
            from unidepth.models import UniDepthV2

            repo = v2b.CHECKPOINTS_DIR / "unidepth" / "lpiccinelli__unidepth-v2-vitl14"
            source = str(repo) if repo.exists() else "lpiccinelli/unidepth-v2-vitl14"
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
            model = UniDepthV2.from_pretrained(source).to(device).eval()
            self._unidepth_v2 = (model, device)

        from PIL import Image
        import torch

        model, device = self._unidepth_v2
        arr = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.uint8)
        rgb = torch.from_numpy(arr).permute(2, 0, 1).to(device)
        with torch.inference_mode():
            out = model.infer(rgb)

        depth = out["depth"].detach().float().cpu().numpy()
        # UniDepth may emit [1,1,H,W] or [1,H,W].
        while depth.ndim > 2 and depth.shape[0] == 1:
            depth = depth.squeeze(0)
        if depth.ndim == 3 and depth.shape[0] != 1 and depth.shape[-1] == 1:
            depth = depth[..., 0]
        if depth.ndim != 2:
            raise RuntimeError(f"Unexpected UniDepth depth shape: {depth.shape}")
        intr = out.get("intrinsics")
        if intr is not None:
            intrinsics = intr.detach().float().cpu().numpy()
            if intrinsics.ndim == 3:
                intrinsics = intrinsics.squeeze(0)
        else:
            intrinsics = _default_intrinsics(depth.shape[1], depth.shape[0])

        points = out.get("points")
        if points is not None:
            pts = points.detach().float().cpu().numpy()
            if pts.ndim == 4:
                pts = pts.squeeze(0)
            if pts.ndim == 3 and pts.shape[0] == 3 and pts.shape[-1] != 3:
                pts = np.transpose(pts, (1, 2, 0))
            if pts.ndim != 3 or pts.shape[-1] != 3:
                pts = _depth_to_points(depth.astype(np.float32), intrinsics.astype(np.float32))
        else:
            pts = _depth_to_points(depth.astype(np.float32), intrinsics.astype(np.float32))

        valid = np.isfinite(depth) & (depth > 0.05)
        normals = _points_to_normals(pts, valid)
        return v2b.MoGeResult(
            points=np.asarray(pts, dtype=np.float32),
            depth=np.asarray(depth, dtype=np.float32),
            normals=normals,
            mask=valid.astype(bool),
            intrinsics=np.asarray(intrinsics, dtype=np.float32),
        )

    def _infer_depth_anything(self, image_path: Path) -> v2b.MoGeResult:
        if self._depth_anything is None:
            from cv_pipeline.depth.depth_anything_v2_metric import DepthAnythingV2Metric, DepthConfig
            from cv_pipeline.paths import VolumePaths

            vol = VolumePaths(root=v2b.VOLUME_ROOT)
            model = DepthAnythingV2Metric(vol, DepthConfig(encoder="vitl", dataset="hypersim", input_size=518))
            self._depth_anything = model

        pred = self._depth_anything.infer(image_path)
        depth = np.asarray(pred.depth_m, dtype=np.float32)
        h, w = depth.shape[:2]
        intrinsics = _default_intrinsics(w, h)
        points = _depth_to_points(depth, intrinsics)
        valid = np.isfinite(depth) & (depth > 0.05)
        normals = _points_to_normals(points, valid)
        return v2b.MoGeResult(
            points=points,
            depth=depth,
            normals=normals,
            mask=valid.astype(bool),
            intrinsics=intrinsics.astype(np.float32),
        )

    def _infer_zoedepth(self, image_path: Path) -> v2b.MoGeResult:
        if self._zoe is None:
            import torch
            from transformers import AutoImageProcessor, ZoeDepthForDepthEstimation

            model_name = "Intel/zoedepth-nyu-kitti"
            processor = AutoImageProcessor.from_pretrained(model_name)
            model = ZoeDepthForDepthEstimation.from_pretrained(model_name)
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
            self._zoe = (processor, model.to(device).eval(), device)

        from PIL import Image
        import cv2
        import torch

        processor, model, device = self._zoe
        image = Image.open(image_path).convert("RGB")
        w, h = image.size
        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            out = model(**inputs)
        depth = out.predicted_depth.detach().float().cpu().numpy()
        if depth.ndim == 3:
            depth = depth.squeeze(0)
        if depth.shape != (h, w):
            depth = cv2.resize(depth.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
        depth = np.asarray(depth, dtype=np.float32)
        intrinsics = _default_intrinsics(w, h)
        points = _depth_to_points(depth, intrinsics)
        valid = np.isfinite(depth) & (depth > 0.05)
        normals = _points_to_normals(points, valid)
        return v2b.MoGeResult(
            points=points,
            depth=depth,
            normals=normals,
            mask=valid.astype(bool),
            intrinsics=intrinsics.astype(np.float32),
        )

    def _infer_metric3d(self, image_path: Path) -> v2b.MoGeResult:
        if self._metric3d is None:
            from cv_pipeline.depth.metric3d_v2 import Metric3DConfig, Metric3DV2
            from cv_pipeline.paths import VolumePaths

            vol = VolumePaths(root=v2b.VOLUME_ROOT)
            model = Metric3DV2(vol, Metric3DConfig(model="vit_small"))
            self._metric3d = model
        pred = self._metric3d.infer(image_path)
        depth = np.asarray(pred.depth_m, dtype=np.float32)
        h, w = depth.shape[:2]
        intrinsics = _default_intrinsics(w, h)
        points = _depth_to_points(depth, intrinsics)
        valid = np.isfinite(depth) & (depth > 0.05)
        normals = _points_to_normals(points, valid)
        return v2b.MoGeResult(
            points=points,
            depth=depth,
            normals=normals,
            mask=valid.astype(bool),
            intrinsics=intrinsics.astype(np.float32),
        )


def _fit_plane(points: np.ndarray, method: str, distance_thresh: float) -> tuple[v2b.Plane, np.ndarray]:
    rng = np.random.default_rng(42)
    if method == "ransac":
        return v2b._ransac_plane(points, distance_thresh=distance_thresh, rng=rng)

    if method == "manhattan":
        plane, _inliers = v2b._ransac_plane(points, distance_thresh=distance_thresh, rng=rng)
        n = np.asarray(plane.normal, dtype=np.float64)
        axis = int(np.argmax(np.abs(n)))
        snapped = np.zeros(3, dtype=np.float64)
        snapped[axis] = 1.0 if n[axis] >= 0 else -1.0
        d = -float(np.median(points @ snapped))
        out_plane = v2b.Plane(normal=snapped.astype(np.float32), d=d)
        inliers = np.abs(out_plane.signed_distance(points)) < distance_thresh
        return out_plane, inliers

    if method == "huber":
        plane, inliers = v2b._ransac_plane(points, distance_thresh=distance_thresh, rng=rng)
        pts = points[inliers]
        if pts.shape[0] < 30:
            return plane, inliers
        p = plane
        for _ in range(8):
            residuals = p.signed_distance(pts)
            med = float(np.median(residuals))
            mad = float(np.median(np.abs(residuals - med)))
            sigma = max(1e-5, 1.4826 * mad)
            c = 1.5 * sigma
            w = np.ones_like(residuals, dtype=np.float64)
            big = np.abs(residuals) > c
            w[big] = c / np.abs(residuals[big])
            ws = w.sum()
            if ws < 1e-8:
                break
            mean = (pts * w[:, None]).sum(axis=0) / ws
            q = pts - mean[None, :]
            cov = (q * w[:, None]).T @ q / ws
            eigvals, eigvecs = np.linalg.eigh(cov)
            n = eigvecs[:, int(np.argmin(eigvals))]
            n_norm = float(np.linalg.norm(n))
            if n_norm < 1e-8:
                break
            n = n / n_norm
            d = -float(n @ mean)
            p = v2b.Plane(normal=n.astype(np.float32), d=d)
        inliers = np.abs(p.signed_distance(points)) < distance_thresh
        return p, inliers

    raise ValueError(f"Unknown plane fit method: {method}")


def _poly_from_points(points2d: np.ndarray, method: str):
    if method == "alpha":
        alpha = v2b._auto_alpha(points2d)
        poly = v2b._alpha_shape(points2d, alpha)
        return poly, float(alpha)

    if method == "concave_adaptive":
        from shapely import MultiPoint
        try:
            from shapely import concave_hull
        except Exception:
            concave_hull = None

        if concave_hull is not None:
            hull = concave_hull(MultiPoint(points2d), ratio=0.12, allow_holes=False)
            if hull is not None and not hull.is_empty and getattr(hull, "area", 0.0) > 0:
                return hull, -1.0
        alpha = max(1e-6, 0.65 * v2b._auto_alpha(points2d))
        poly = v2b._alpha_shape(points2d, alpha)
        return poly, float(alpha)

    if method == "occupancy_grid":
        import cv2
        from shapely.geometry import Polygon

        res = 0.03  # meters/cell
        xmin, ymin = np.min(points2d, axis=0)
        xmax, ymax = np.max(points2d, axis=0)
        w = max(40, int(np.ceil((xmax - xmin) / res)) + 8)
        h = max(40, int(np.ceil((ymax - ymin) / res)) + 8)
        grid = np.zeros((h, w), dtype=np.uint8)
        gx = np.clip(np.round((points2d[:, 0] - xmin) / res).astype(np.int32), 0, w - 1)
        gy = np.clip(np.round((points2d[:, 1] - ymin) / res).astype(np.int32), 0, h - 1)
        grid[gy, gx] = 255
        k = np.ones((3, 3), dtype=np.uint8)
        grid = cv2.dilate(grid, k, iterations=2)
        grid = cv2.morphologyEx(grid, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8), iterations=2)
        contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            alpha = v2b._auto_alpha(points2d)
            poly = v2b._alpha_shape(points2d, alpha)
            return poly, float(alpha)
        c = max(contours, key=cv2.contourArea).reshape(-1, 2)
        xy = np.empty_like(c, dtype=np.float64)
        xy[:, 0] = xmin + c[:, 0] * res
        xy[:, 1] = ymin + c[:, 1] * res
        poly = Polygon(xy)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly, 0.0

    raise ValueError(f"Unknown boundary method: {method}")


def make_compute_floor_area_fn(plane_fit: str, boundary: str):
    def _fn(
        floor_mask: np.ndarray,
        moge: v2b.MoGeResult,
        *,
        normal_thresh: float = 0.5,
        distance_thresh: float = 0.04,
        return_debug: bool = False,
    ):
        valid = np.asarray(floor_mask, dtype=bool) & np.asarray(moge.mask, dtype=bool)
        if moge.normals is not None and np.asarray(moge.normals).size > 0:
            ny = np.asarray(moge.normals[..., 1], dtype=np.float32)
            valid &= np.abs(ny) > normal_thresh
        if int(valid.sum()) < 100:
            return 0.0, 0.0, None, float("inf"), 0, None

        pts = np.asarray(moge.points[valid], dtype=np.float64)
        finite = np.isfinite(pts).all(axis=1)
        pts = pts[finite]
        if pts.shape[0] < 40:
            return 0.0, 0.0, None, float("inf"), 0, None
        if pts.shape[0] > 120_000:
            rng = np.random.default_rng(52)
            idx = rng.choice(pts.shape[0], size=120_000, replace=False)
            pts = pts[idx]

        plane, inliers = _fit_plane(pts, method=plane_fit, distance_thresh=distance_thresh)
        inlier_pts = pts[inliers]
        if inlier_pts.shape[0] < 12:
            return 0.0, 0.0, plane, float("inf"), int(inliers.sum()), None
        residuals = np.abs(plane.signed_distance(inlier_pts))
        rms = float(np.sqrt(np.mean(residuals * residuals)))

        pts2d = v2b._project_to_plane(inlier_pts, plane)
        poly, alpha = _poly_from_points(pts2d, method=boundary)
        area_m2 = float(getattr(poly, "area", 0.0))
        if not np.isfinite(area_m2) or area_m2 <= 0:
            return 0.0, 0.0, plane, rms, int(inliers.sum()), None

        debug = None
        if return_debug:
            coords = None
            if hasattr(poly, "exterior") and poly.exterior is not None:
                coords = np.asarray(poly.exterior.coords, dtype=np.float32)
            debug = v2b.AreaDebug(
                pts2d=np.asarray(pts2d, dtype=np.float32),
                poly_coords=coords,
                alpha=float(alpha),
                n_inliers=int(inliers.sum()),
            )

        return area_m2, area_m2 * v2b.SQFT_PER_M2, plane, rms, int(inliers.sum()), debug

    return _fn


def _infer_mast3r_scene(images: list[Path], *, niter: int) -> v2b.Dust3RRecon:
    from cv_pipeline.paths import VolumePaths
    from cv_pipeline.reconstruction.mast3r_backend import MASt3RConfig, run_mast3r_reconstruction

    vol = VolumePaths(root=v2b.VOLUME_ROOT)
    work_dir = REPO_ROOT / "v2-pipeline" / ".work_mast3r"
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = MASt3RConfig(niter=int(niter), image_size=512, batch_size=1)
    out = run_mast3r_reconstruction(images=images, volume=vol, work_dir=work_dir, cfg=cfg)
    return v2b.Dust3RRecon(
        depthmaps=[np.asarray(d, dtype=np.float32) for d in out.depthmaps],
        pts3d=[np.asarray(p, dtype=np.float32) for p in out.points_world],
        masks=[np.asarray(m, dtype=bool) for m in out.masks],
        cam2world=np.asarray(out.cam2world, dtype=np.float64),
        intrinsics=np.asarray(out.intrinsics, dtype=np.float64),
    )


def make_scene_multiview_fn(
    *,
    reconstruction: str,
    scale_anchor: str,
    plane_fit: str,
    boundary: str,
    floor_source: str,
    depth_backends: DepthBackends,
):
    def _fn(
        *,
        images: list[Path],
        floor_masks: list[np.ndarray],
        moge_results: list[v2b.MoGeResult],
        debug_dir: Path | None,
        impute_room_corners: bool,
        dust3r_niter: int,
    ) -> dict[str, object]:
        t0 = time.time()
        if reconstruction == "dust3r":
            recon = v2b.infer_dust3r_scene(images, niter=dust3r_niter)
        elif reconstruction in {"mast3r", "mast3r_metric_direct"}:
            recon = _infer_mast3r_scene(images, niter=dust3r_niter)
        else:
            raise ValueError(f"Unknown reconstruction backend: {reconstruction}")

        # scale anchor
        if scale_anchor == "moge":
            metric_depths = [np.asarray(m.depth, dtype=np.float32) for m in moge_results]
            scale, rel_std = v2b.estimate_scene_scale_m_per_unit(recon.depthmaps, metric_depths, recon.masks)
        elif scale_anchor == "unidepth_v2":
            metric_depths = [depth_backends.metric_depth_only("unidepth_v2", p) for p in images]
            scale, rel_std = v2b.estimate_scene_scale_m_per_unit(recon.depthmaps, metric_depths, recon.masks)
        elif scale_anchor == "hybrid_moge_unidepth":
            metric_depths_m = [np.asarray(m.depth, dtype=np.float32) for m in moge_results]
            metric_depths_u = [depth_backends.metric_depth_only("unidepth_v2", p) for p in images]
            s1, r1 = v2b.estimate_scene_scale_m_per_unit(recon.depthmaps, metric_depths_m, recon.masks)
            s2, r2 = v2b.estimate_scene_scale_m_per_unit(recon.depthmaps, metric_depths_u, recon.masks)
            scale = float(np.median(np.array([s1, s2], dtype=np.float64)))
            rel_std = float(np.std(np.array([s1, s2], dtype=np.float64)) / max(scale, 1e-9) + 0.5 * (r1 + r2))
        elif scale_anchor == "mast3r_metric_direct":
            scale = 1.0
            rel_std = 0.0
        else:
            raise ValueError(f"Unknown scale anchor: {scale_anchor}")

        cam2world_metric = np.asarray(recon.cam2world, dtype=np.float64).copy()
        cam2world_metric[:, :3, 3] *= float(scale)
        cam_centers = cam2world_metric[:, :3, 3]

        points_list: list[np.ndarray] = []
        src_list: list[np.ndarray] = []
        n_inputs = min(len(images), len(floor_masks), len(moge_results), len(recon.masks), len(recon.pts3d))
        for i in range(n_inputs):
            floor_mask = np.asarray(floor_masks[i], dtype=bool)
            if floor_source == "moge":
                m = moge_results[i]
                if floor_mask.shape != m.depth.shape:
                    import cv2

                    floor_mask = cv2.resize(
                        floor_mask.astype(np.uint8),
                        (m.depth.shape[1], m.depth.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    ).astype(bool)
                valid = floor_mask & np.asarray(m.mask, dtype=bool)
                if m.normals is not None and np.asarray(m.normals).size > 0:
                    valid &= np.abs(np.asarray(m.normals[..., 1], dtype=np.float32)) > 0.5
                if int(valid.sum()) < 300:
                    continue
                pts_cam = np.asarray(m.points[valid], dtype=np.float64)
                pts_cam = pts_cam[np.isfinite(pts_cam).all(axis=1)]
                if pts_cam.shape[0] < 300:
                    continue
                R = cam2world_metric[i, :3, :3].astype(np.float64)
                t = cam2world_metric[i, :3, 3].astype(np.float64)
                pts_world = pts_cam @ R.T + t[None, :]
            elif floor_source == "recon":
                import cv2

                pts_view = np.asarray(recon.pts3d[i], dtype=np.float64)
                msk = np.asarray(recon.masks[i], dtype=bool)
                if floor_mask.shape != msk.shape:
                    floor_mask = cv2.resize(
                        floor_mask.astype(np.uint8),
                        (msk.shape[1], msk.shape[0]),
                        interpolation=cv2.INTER_NEAREST,
                    ).astype(bool)
                valid = floor_mask & msk & np.isfinite(pts_view).all(axis=2)
                if int(valid.sum()) < 300:
                    continue
                pts_world = np.asarray(pts_view[valid], dtype=np.float64) * float(scale)
            else:
                raise ValueError(f"Unknown floor_source: {floor_source}")

            if pts_world.shape[0] > 300_000:
                rng = np.random.default_rng(91 + i)
                idx = rng.choice(pts_world.shape[0], size=300_000, replace=False)
                pts_world = pts_world[idx]
            points_list.append(pts_world)
            src_list.append(np.full((pts_world.shape[0],), i, dtype=np.int32))

        if len(points_list) < 2:
            return {"ok": False, "reason": "insufficient_floor_points"}
        points = np.concatenate(points_list, axis=0)
        point_sources = np.concatenate(src_list, axis=0)
        if points.shape[0] < 1500:
            return {"ok": False, "reason": "too_few_points", "n_points": int(points.shape[0])}

        gravity_up = v2b._estimate_gravity_up(cam2world_metric)
        plane, inliers, floor_score = v2b._find_floor_plane_in_scene(
            points, cam_centers, gravity_up, distance_thresh=0.05
        )
        if plane_fit == "m_estimator":
            inlier_pts = points[inliers]
            if inlier_pts.shape[0] > 100:
                p2, in2 = _fit_plane(inlier_pts, method="huber", distance_thresh=0.05)
                # keep orientation coherent with gravity
                if abs(float(np.dot(p2.normal, gravity_up))) > 0.5:
                    plane = p2
                    inliers = np.abs(plane.signed_distance(points)) < 0.05

        inlier_pts = points[inliers]
        if inlier_pts.shape[0] < 1200:
            return {"ok": False, "reason": "too_few_floor_inliers", "n_inliers": int(inlier_pts.shape[0])}
        residuals = np.abs(plane.signed_distance(inlier_pts))
        plane_residual = float(np.sqrt(np.mean(residuals * residuals)))

        pts2d = v2b._project_to_plane(inlier_pts, plane)
        poly, alpha = _poly_from_points(pts2d, method=boundary)
        visible_m2 = float(getattr(poly, "area", 0.0))
        if not np.isfinite(visible_m2) or visible_m2 <= 0:
            return {"ok": False, "reason": "invalid_visible_area"}

        completed = poly
        completed_m2 = visible_m2
        rect_upper_m2 = visible_m2
        completion_mode = "visible-only"
        completion_info: dict[str, object] = {"strategy": "none"}
        if impute_room_corners:
            rect, completion_info = v2b._completion_rectangle(poly, "manhattan-rect")
            rect_upper_m2 = max(float(rect.area), visible_m2)
            completed = rect
            completed_m2 = 0.5 * (visible_m2 + rect_upper_m2)
            completion_mode = "midpoint-between-bounds"

        poly_coords = (
            np.asarray(poly.exterior.coords, dtype=np.float32)
            if hasattr(poly, "exterior") and poly.exterior is not None
            else None
        )
        rect_coords = (
            np.asarray(completed.exterior.coords, dtype=np.float32)
            if hasattr(completed, "exterior") and completed.exterior is not None
            else None
        )
        reproj_ious: list[float] = []
        if poly_coords is not None and poly_coords.shape[0] >= 4:
            import cv2

            nvec = np.asarray(plane.normal, dtype=np.float64)
            nvec = nvec / max(1e-9, float(np.linalg.norm(nvec)))
            u, vv = v2b._plane_basis(nvec)
            p0 = -float(plane.d) * nvec
            poly3d = p0[None, :] + poly_coords[:, [0]].astype(np.float64) * u[None, :] + poly_coords[:, [1]].astype(
                np.float64
            ) * vv[None, :]
            for i in range(n_inputs):
                h_i, w_i = recon.depthmaps[i].shape[:2]
                fm = np.asarray(floor_masks[i], dtype=bool)
                if fm.shape != (h_i, w_i):
                    fm = cv2.resize(
                        fm.astype(np.uint8),
                        (w_i, h_i),
                        interpolation=cv2.INTER_NEAREST,
                    ).astype(bool)
                uv_poly, valid_poly = v2b._project_world_to_image(poly3d, cam2world_metric[i], recon.intrinsics[i])
                if int(np.sum(valid_poly)) < 4:
                    continue
                poly_uv = np.round(uv_poly[valid_poly]).astype(np.int32).reshape((-1, 1, 2))
                pred = np.zeros((h_i, w_i), dtype=np.uint8)
                cv2.fillPoly(pred, [poly_uv], 1)
                tgt = fm.astype(np.uint8)
                inter = int(np.sum((pred == 1) & (tgt == 1)))
                union = int(np.sum((pred == 1) | (tgt == 1)))
                if union > 0:
                    reproj_ious.append(float(inter) / float(union))
        cam2d = v2b._project_to_plane(cam_centers, plane)
        artifacts: dict[str, str] = {}
        if debug_dir is not None:
            scene_dir = debug_dir / "scene_pose_moge_floor"
            artifacts.update(
                v2b._save_scene_floor_artifacts(
                    points2d=pts2d.astype(np.float32),
                    point_sources=point_sources[inliers],
                    poly_coords=poly_coords,
                    rect_coords=rect_coords if impute_room_corners else None,
                    cam2d=cam2d.astype(np.float32),
                    out_dir=scene_dir,
                )
            )
            artifacts.update(
                v2b._save_scene_reprojection_artifacts(
                    images=images,
                    recon=recon,
                    cam2world_metric=cam2world_metric,
                    floor_points_world=inlier_pts.astype(np.float32),
                    floor_point_sources=point_sources[inliers].astype(np.int32),
                    floor_poly_coords_2d=poly_coords,
                    floor_plane=plane,
                    out_dir=scene_dir,
                )
            )
            artifacts.update(
                v2b._save_scene_3d_artifacts(
                    recon=recon,
                    images=images,
                    scale_m_per_unit=float(scale),
                    floor_points_world=inlier_pts.astype(np.float32),
                    floor_point_sources=point_sources[inliers].astype(np.int32),
                    cam_centers=cam_centers.astype(np.float32),
                    out_dir=scene_dir,
                )
            )

        return {
            "ok": True,
            "method": f"{reconstruction}+{floor_source}+{scale_anchor}+{plane_fit}+{boundary}",
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
            "scale_rel_std": float(rel_std),
            "reprojection_iou_mean": None if not reproj_ious else float(np.mean(reproj_ious)),
            "reprojection_iou_per_view": [float(x) for x in reproj_ious],
            "floor_score": float(floor_score),
            "n_images_fused": int(len(points_list)),
            "source_inliers": {
                str(i): int(np.sum(point_sources[inliers] == i))
                for i in range(n_inputs)
            },
            "timings_s": {"total": float(time.time() - t0)},
            "artifacts": artifacts,
        }

    return _fn


def _choose_key_visual(debug_dir: Path) -> str | None:
    candidates = [
        debug_dir / "multiview" / "dust3r-scene" / "scene_pose_moge_floor" / "reproject_gallery.png",
        debug_dir / "multiview" / "dust3r-scene" / "scene_pose_moge_floor" / "reproject_stitched_floor_overlay.png",
        debug_dir / "multiview" / "dust3r-scene" / "scene_pose_moge_floor" / "scene_floor_topdown.png",
    ]
    candidates.extend(sorted(debug_dir.glob("per_image/*/floor_overlay.jpg")))
    candidates.extend(sorted(debug_dir.rglob("reproject_gallery.png")))
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _torch_peak_vram_mb() -> float | None:
    try:
        import torch
    except Exception:
        return None
    if torch.cuda.is_available():
        return float(torch.cuda.max_memory_allocated() / (1024.0 * 1024.0))
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        # MPS exposes current allocation but not robust peak in many versions.
        cur = None
        try:
            cur = float(torch.mps.current_allocated_memory() / (1024.0 * 1024.0))
        except Exception:
            cur = None
        return cur
    return None


def _torch_reset_peak():
    try:
        import torch
    except Exception:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


@contextmanager
def _patch_v2b(patches: dict[str, object]):
    old: dict[str, object] = {}
    try:
        for k, v in patches.items():
            old[k] = getattr(v2b, k)
            setattr(v2b, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(v2b, k, v)


def _download_missing_assets() -> None:
    if not CV_PIPELINE_DOWNLOAD_SCRIPT.exists():
        return
    project = REPO_ROOT / "cv-pipeline"
    base = ["uv", "run", "--project", str(project), "python", str(CV_PIPELINE_DOWNLOAD_SCRIPT)]
    commands = [
        [*base, "vendor-all"],
        [*base, "depth-anything-metric", "--encoder", "vitl", "--dataset", "hypersim"],
        [*base, "metric3d", "--model", "vit_small"],
        [*base, "metric3d", "--model", "vit_large"],
        [*base, "unidepth", "--repo", "lpiccinelli/unidepth-v1-vitl14"],
        [*base, "unidepth", "--repo", "lpiccinelli/unidepth-v2-vitl14"],
        [*base, "moge", "--repo", "Ruicheng/moge-2-vitl-normal"],
        [*base, "dust3r", "--model", "vitl_512_dpt"],
        [*base, "mast3r", "--with-retrieval"],
    ]
    for cmd in commands:
        subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)


def _load_cases(args: argparse.Namespace) -> list[BenchCase]:
    if args.cases_json is not None:
        obj = json.loads(args.cases_json.read_text())
        rows = obj.get("cases", obj)
        cases: list[BenchCase] = []
        for row in rows:
            name = str(row["name"])
            known = row.get("known_room_sqft")
            if "images" in row:
                images = [Path(x).expanduser().resolve() for x in row["images"]]
            else:
                folder = Path(row["images_dir"]).expanduser().resolve()
                all_images = _list_images(folder)
                selected = row.get("image_names")
                if selected:
                    want = set(selected)
                    images = [p for p in all_images if p.name in want]
                else:
                    images = all_images
            if args.max_images and len(images) > args.max_images:
                images = images[: args.max_images]
            cases.append(BenchCase(name=name, images=images, known_room_sqft=None if known is None else float(known)))
        return cases

    if args.images_dir is not None:
        folder = args.images_dir.expanduser().resolve()
        images = _list_images(folder)
        if args.max_images and len(images) > args.max_images:
            images = images[: args.max_images]
        if not images:
            raise RuntimeError(f"No images found in {folder}")
        return [BenchCase(name=folder.name, images=images, known_room_sqft=args.known_room_sqft)]

    # fallback default: first listing from clean export/eval dataset
    default_dirs = [
        REPO_ROOT / "sample-collection" / "clean_set_export" / "photos" / "listing_001",
        REPO_ROOT / "sample-collection" / "streeteasy_eval_dataset" / "photos" / "listing_001",
    ]
    for d in default_dirs:
        if d.is_dir():
            images = _list_images(d)
            if args.max_images and len(images) > args.max_images:
                images = images[: args.max_images]
            if images:
                return [BenchCase(name=d.name, images=images, known_room_sqft=args.known_room_sqft)]
    raise RuntimeError("No default benchmark images found. Pass --images-dir or --cases-json.")


def _build_specs(args: argparse.Namespace) -> list[RunSpec]:
    specs: list[RunSpec] = []
    # Baselines (exact v2b path)
    specs.append(
        RunSpec(
            track="single",
            category="baseline",
            name="v2b_single_exact",
            baseline_exact_v2b=True,
        )
    )
    specs.append(
        RunSpec(
            track="multiview",
            category="baseline",
            name="v2b_dust3r_scene_exact",
            baseline_exact_v2b=True,
        )
    )

    # 1) floor segmentation (both pipelines)
    for seg in ("mask2former_ade20k", "oneformer_ade20k", "sam2_prompt_floor"):
        specs.append(
            RunSpec(track="single", category="segmentation", name=f"single_seg_{seg}", segmentation=seg)
        )
        specs.append(
            RunSpec(track="multiview", category="segmentation", name=f"multiview_seg_{seg}", segmentation=seg)
        )

    # 2) single-image metric geometry swaps
    for depth in ("unidepth_v2", "metric3d_v2", "depth_anything_v2_metric", "zoedepth_metric"):
        specs.append(
            RunSpec(track="single", category="single_metric_geometry", name=f"single_depth_{depth}", depth=depth)
        )

    # 3) multi-view reconstruction swaps (+ two MASt3R variants)
    specs.append(
        RunSpec(
            track="multiview",
            category="multiview_reconstruction",
            name="multiview_recon_mast3r_pose_moge_floor",
            reconstruction="mast3r",
        )
    )
    specs.append(
        RunSpec(
            track="multiview",
            category="multiview_reconstruction",
            name="multiview_recon_mast3r_metric_direct",
            reconstruction="mast3r_metric_direct",
            scale_anchor="mast3r_metric_direct",
            floor_source="recon",
        )
    )

    # 4) scale anchoring swaps (multi-view)
    specs.append(
        RunSpec(
            track="multiview",
            category="scale_anchor",
            name="multiview_scale_unidepth_v2",
            scale_anchor="unidepth_v2",
        )
    )
    specs.append(
        RunSpec(
            track="multiview",
            category="scale_anchor",
            name="multiview_scale_hybrid_moge_unidepth",
            scale_anchor="hybrid_moge_unidepth",
        )
    )

    # 5) floor point filtering / plane fit
    specs.append(
        RunSpec(track="single", category="plane_fit", name="single_plane_huber", plane_fit="huber")
    )
    specs.append(
        RunSpec(track="single", category="plane_fit", name="single_plane_manhattan", plane_fit="manhattan")
    )
    specs.append(
        RunSpec(track="multiview", category="plane_fit", name="multiview_plane_m_estimator", plane_fit="m_estimator")
    )

    # 6) boundary construction / fused patch
    for b in ("occupancy_grid", "concave_adaptive"):
        specs.append(
            RunSpec(track="single", category="boundary", name=f"single_boundary_{b}", boundary=b)
        )
        specs.append(
            RunSpec(track="multiview", category="boundary", name=f"multiview_boundary_{b}", boundary=b)
        )

    if args.single_only:
        specs = [s for s in specs if s.track == "single"]
    if args.multiview_only:
        specs = [s for s in specs if s.track == "multiview"]
    return specs


def _run_with_spec(
    *,
    case: BenchCase,
    spec: RunSpec,
    out_root: Path,
    seg_backends: SegmentationBackends,
    depth_backends: DepthBackends,
    dust3r_iters: int,
) -> RunRecord:
    debug_dir = out_root / "photos" / spec.track / spec.category / spec.name / case.name
    debug_dir.mkdir(parents=True, exist_ok=True)

    patches: dict[str, object] = {}
    if not spec.baseline_exact_v2b:
        if spec.segmentation != "segformer_b5_ade20k":
            patches["segment_floor"] = lambda image_path: seg_backends.segment(spec.segmentation, image_path)
        if spec.depth != "moge2":
            patches["infer_moge"] = lambda image_path: depth_backends.infer(spec.depth, image_path)
        if spec.track == "single" and (spec.plane_fit != "ransac" or spec.boundary != "alpha"):
            patches["compute_floor_area_single_image"] = make_compute_floor_area_fn(spec.plane_fit, spec.boundary)
        if spec.track == "multiview":
            needs_custom_scene = (
                spec.plane_fit != "ransac"
                or spec.boundary != "alpha"
                or spec.floor_source != "moge"
                or spec.reconstruction in {"mast3r_metric_direct"}
                or spec.scale_anchor not in {"moge"}
            )
            if spec.reconstruction == "mast3r":
                patches["infer_dust3r_scene"] = lambda images, niter=dust3r_iters: _infer_mast3r_scene(
                    images, niter=int(niter)
                )
            if needs_custom_scene:
                patches["scene_multiview_floor"] = make_scene_multiview_fn(
                    reconstruction=spec.reconstruction,
                    scale_anchor=spec.scale_anchor,
                    plane_fit=spec.plane_fit,
                    boundary=spec.boundary,
                    floor_source=spec.floor_source,
                    depth_backends=depth_backends,
                )

    _torch_reset_peak()
    rss_before = float(v2b._max_rss_mb())
    t0 = time.time()
    try:
        with _patch_v2b(patches):
            result = v2b.run_pipeline(
                case.images,
                interactive=False,
                debug_dir=debug_dir,
                impute_room_corners=False,
                multiview_method="single-image" if spec.track == "single" else "dust3r-scene",
                dust3r_niter=max(20, int(dust3r_iters)),
            )
        runtime_s = float(time.time() - t0)
        area_sqft = float(result.sqft)
        ci_lo = float(result.ci_lo)
        ci_hi = float(result.ci_hi)
        known = case.known_room_sqft
        abs_err = None if known is None else abs(area_sqft - float(known))
        pct_err = None if known is None or float(known) == 0 else 100.0 * abs_err / float(known)
        iou = None
        if spec.track == "multiview":
            mv = result.diagnostics.get("multiview", {})
            selected = mv.get("selected")
            candidates = mv.get("candidates", {})
            if selected and selected in candidates:
                cand = candidates[selected]
                if isinstance(cand, dict) and "reprojection_iou_mean" in cand:
                    try:
                        iou = float(cand["reprojection_iou_mean"])
                    except Exception:
                        iou = None

        return RunRecord(
            case=case.name,
            track=spec.track,
            category=spec.category,
            config=spec.name,
            status="ok",
            method=result.method,
            area_sqft=area_sqft,
            ci_lo_sqft=ci_lo,
            ci_hi_sqft=ci_hi,
            known_room_sqft=known,
            abs_error_sqft=abs_err,
            pct_error=pct_err,
            runtime_s=runtime_s,
            peak_rss_delta_mb=max(0.0, float(v2b._max_rss_mb()) - rss_before),
            peak_vram_mb=_torch_peak_vram_mb(),
            reprojection_iou_mean=iou,
            debug_dir=str(debug_dir),
            key_visual=_choose_key_visual(debug_dir),
            error=None,
        )
    except Exception as ex:
        return RunRecord(
            case=case.name,
            track=spec.track,
            category=spec.category,
            config=spec.name,
            status="error",
            method=None,
            area_sqft=None,
            ci_lo_sqft=None,
            ci_hi_sqft=None,
            known_room_sqft=case.known_room_sqft,
            abs_error_sqft=None,
            pct_error=None,
            runtime_s=float(time.time() - t0),
            peak_rss_delta_mb=max(0.0, float(v2b._max_rss_mb()) - rss_before),
            peak_vram_mb=_torch_peak_vram_mb(),
            reprojection_iou_mean=None,
            debug_dir=str(debug_dir),
            key_visual=_choose_key_visual(debug_dir),
            error=f"{type(ex).__name__}: {ex}",
        )
    finally:
        gc.collect()


def _write_outputs(out_root: Path, records: list[RunRecord]) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    json_path = out_root / "benchmark_results.json"
    json_path.write_text(json.dumps([asdict(r) for r in records], indent=2) + "\n", encoding="utf-8")

    csv_path = out_root / "benchmark_results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(records[0]).keys()) if records else [])
        if records:
            writer.writeheader()
            for r in records:
                writer.writerow(asdict(r))

    md_path = out_root / "benchmark_summary.md"
    lines: list[str] = []
    lines.append("# v3_benchmakrs Summary")
    lines.append("")
    lines.append("## Single-Image Pipeline")
    lines.append("")
    lines.append("| case | category | config | status | sqft | err_sqft | runtime_s | rss_mb | key_visual |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---|")
    for r in records:
        if r.track != "single":
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    r.case,
                    r.category,
                    r.config,
                    r.status,
                    "" if r.area_sqft is None else f"{r.area_sqft:.1f}",
                    "" if r.abs_error_sqft is None else f"{r.abs_error_sqft:.1f}",
                    "" if r.runtime_s is None else f"{r.runtime_s:.1f}",
                    "" if r.peak_rss_delta_mb is None else f"{r.peak_rss_delta_mb:.1f}",
                    r.key_visual or "",
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("## Multi-View Pipeline")
    lines.append("")
    lines.append("| case | category | config | status | sqft | err_sqft | reproj_iou | runtime_s | rss_mb | key_visual |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---|")
    for r in records:
        if r.track != "multiview":
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    r.case,
                    r.category,
                    r.config,
                    r.status,
                    "" if r.area_sqft is None else f"{r.area_sqft:.1f}",
                    "" if r.abs_error_sqft is None else f"{r.abs_error_sqft:.1f}",
                    "" if r.reprojection_iou_mean is None else f"{r.reprojection_iou_mean:.3f}",
                    "" if r.runtime_s is None else f"{r.runtime_s:.1f}",
                    "" if r.peak_rss_delta_mb is None else f"{r.peak_rss_delta_mb:.1f}",
                    r.key_visual or "",
                ]
            )
            + " |"
        )

    ok = [r for r in records if r.status == "ok" and r.area_sqft is not None]
    if ok:
        lines.append("")
        lines.append("## Quick Stats")
        lines.append("")
        lines.append(f"- total_runs: {len(records)}")
        lines.append(f"- succeeded: {len(ok)}")
        lines.append(f"- failed: {len(records) - len(ok)}")
        lines.append(f"- median_runtime_s: {statistics.median([r.runtime_s for r in ok if r.runtime_s is not None]):.1f}")

    errs = [r for r in ok if r.abs_error_sqft is not None]
    if errs:
        best = min(errs, key=lambda x: float(x.abs_error_sqft))
        lines.append(
            f"- best_abs_error: {best.abs_error_sqft:.1f} sqft ({best.track}/{best.config} on {best.case})"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="v3 benchmark harness for single-image and multi-view floor estimation")
    parser.add_argument("--cases-json", type=Path, default=None, help="JSON file containing benchmark cases.")
    parser.add_argument("--images-dir", type=Path, default=None, help="Single case: directory of room photos.")
    parser.add_argument("--known-room-sqft", type=float, default=None, help="Known room sqft for error metrics.")
    parser.add_argument("--max-images", type=int, default=4, help="Max images per case.")
    parser.add_argument("--dust3r-iters", type=int, default=120, help="DUSt3R/MASt3R alignment iterations.")
    parser.add_argument("--single-only", action="store_true")
    parser.add_argument("--multiview-only", action="store_true")
    parser.add_argument("--download-missing", action="store_true", help="Download/check all model assets first.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "runs" / f"v3_benchmakrs_{_run_stamp()}",
    )
    args = parser.parse_args()

    if args.download_missing:
        _download_missing_assets()

    cases = _load_cases(args)
    for c in cases:
        if not c.images:
            raise RuntimeError(f"Case {c.name} has no images.")

    specs = _build_specs(args)
    out_root = args.out_dir.expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    seg_backends = SegmentationBackends()
    depth_backends = DepthBackends()
    records: list[RunRecord] = []

    print(f"Output root: {out_root}")
    print(f"Cases: {len(cases)}  |  Configs: {len(specs)}")
    for case in cases:
        print(f"\nCase {case.name}: {len(case.images)} images  known_room_sqft={case.known_room_sqft}")
        for i, spec in enumerate(specs, start=1):
            print(f"[{i:02d}/{len(specs):02d}] {spec.track}/{spec.category}/{spec.name} ... ", end="", flush=True)
            rec = _run_with_spec(
                case=case,
                spec=spec,
                out_root=out_root,
                seg_backends=seg_backends,
                depth_backends=depth_backends,
                dust3r_iters=args.dust3r_iters,
            )
            records.append(rec)
            if rec.status == "ok":
                area_txt = "n/a" if rec.area_sqft is None else f"{rec.area_sqft:.1f} sqft"
                rt_txt = "n/a" if rec.runtime_s is None else f"{rec.runtime_s:.1f}s"
                print(f"ok ({area_txt}, {rt_txt})")
            else:
                print(f"error ({rec.error})")

    _write_outputs(out_root, records)
    print("\nSaved:")
    print(f"  - {out_root / 'benchmark_results.json'}")
    print(f"  - {out_root / 'benchmark_results.csv'}")
    print(f"  - {out_root / 'benchmark_summary.md'}")
    print(f"  - {out_root / 'photos'}")


if __name__ == "__main__":
    main()
