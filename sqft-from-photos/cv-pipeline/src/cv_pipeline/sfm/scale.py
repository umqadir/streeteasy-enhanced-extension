from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.sfm.colmap_model import ColmapModel


@dataclass(frozen=True)
class ScaleEstimate:
    scale_m_per_sfm: float
    scale_rel_std: float
    inliers: int
    total: int
    per_image_scales: dict[str, float]
    diagnostics: dict[str, object]


def _bilinear_sample(depth: np.ndarray, x: float, y: float) -> float | None:
    h, w = depth.shape[:2]
    if x < 0 or y < 0 or x >= w - 1 or y >= h - 1:
        return None
    x0 = int(np.floor(x))
    y0 = int(np.floor(y))
    x1 = x0 + 1
    y1 = y0 + 1
    dx = float(x - x0)
    dy = float(y - y0)
    v00 = float(depth[y0, x0])
    v10 = float(depth[y0, x1])
    v01 = float(depth[y1, x0])
    v11 = float(depth[y1, x1])
    if not np.isfinite([v00, v10, v01, v11]).all():
        return None
    return (v00 * (1 - dx) * (1 - dy)) + (v10 * dx * (1 - dy)) + (v01 * (1 - dx) * dy) + (v11 * dx * dy)


def _robust_mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    return 1.4826 * mad


def estimate_scale_from_depth_alignment(
    model: ColmapModel,
    depth_dir: Path,
    *,
    max_depth_m: float = 20.0,
    min_pairs_per_image: int = 200,
) -> ScaleEstimate:
    """
    Fit a global scale s (meters per SfM unit) s.t. z_pred_m ≈ s * z_sfm.
    Depth maps must be in meters and at the same resolution as COLMAP's images.txt coordinates.
    """
    points = model.points3d

    per_image_scales: dict[str, float] = {}
    per_image_weights: list[float] = []
    per_image_values: list[float] = []
    total_pairs = 0
    total_inliers = 0

    for img in model.images.values():
        depth_path = depth_dir / f"{Path(img.name).stem}_raw_depth_meter.npy"
        if not depth_path.exists():
            continue
        depth = np.load(depth_path)
        # Build (z_sfm, z_pred) pairs from tracked 2D observations.
        z_sfm_list: list[float] = []
        z_pred_list: list[float] = []
        if img.xys.shape[0] == 0:
            continue

        # Filter to points with known 3D ids and present in points3d.
        ok = img.point3d_ids >= 0
        if ok.any():
            pids_raw = img.point3d_ids[ok]
            keep = np.array([int(pid) in points for pid in pids_raw], dtype=bool)
            xys = img.xys[ok][keep]
            pids = pids_raw[keep]
        else:
            xys = img.xys[:0]
            pids = img.point3d_ids[:0]
        if xys.shape[0] == 0:
            continue

        # Pre-fetch point XYZs for speed.
        xyz = np.stack([points[int(pid)].xyz for pid in pids], axis=0)
        cam = img.world_to_cam(xyz)
        z_sfm = cam[:, 2]

        for (x, y), z in zip(xys, z_sfm, strict=True):
            if z <= 1e-6 or not np.isfinite(z):
                continue
            z_pred = _bilinear_sample(depth, float(x), float(y))
            if z_pred is None or z_pred <= 1e-6 or z_pred > max_depth_m:
                continue
            z_sfm_list.append(float(z))
            z_pred_list.append(float(z_pred))

        if len(z_sfm_list) < min_pairs_per_image:
            continue

        z_sfm_arr = np.asarray(z_sfm_list, dtype=np.float64)
        z_pred_arr = np.asarray(z_pred_list, dtype=np.float64)
        ratios = z_pred_arr / z_sfm_arr

        med = float(np.median(ratios))
        mad = _robust_mad(ratios)
        if not np.isfinite(med) or med <= 0:
            continue

        # Inliers in ratio-space (robust z-score).
        if mad > 0:
            z = np.abs((ratios - med) / mad)
            inlier = z < 3.5
        else:
            inlier = np.ones_like(ratios, dtype=bool)

        if int(inlier.sum()) < min_pairs_per_image:
            continue

        scale_i = float(np.median(ratios[inlier]))
        per_image_scales[img.name] = scale_i

        weight = float(inlier.sum())
        per_image_weights.append(weight)
        per_image_values.append(scale_i)
        total_pairs += int(ratios.shape[0])
        total_inliers += int(inlier.sum())

    if not per_image_values:
        raise RuntimeError(
            "Failed to estimate scale: no images produced enough depth/SfM alignment pairs. "
            "Check that depth maps exist and match COLMAP image resolutions."
        )

    values = np.asarray(per_image_values, dtype=np.float64)
    weights = np.asarray(per_image_weights, dtype=np.float64)
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    cum = np.cumsum(weights)
    cutoff = 0.5 * float(cum[-1])
    idx = int(np.searchsorted(cum, cutoff))
    scale = float(values[min(idx, values.shape[0] - 1)])

    rel_std = float(_robust_mad(values) / max(scale, 1e-9))

    return ScaleEstimate(
        scale_m_per_sfm=scale,
        scale_rel_std=rel_std,
        inliers=total_inliers,
        total=total_pairs,
        per_image_scales=per_image_scales,
        diagnostics={
            "images_used": len(per_image_scales),
            "min_pairs_per_image": min_pairs_per_image,
            "max_depth_m": max_depth_m,
        },
    )
