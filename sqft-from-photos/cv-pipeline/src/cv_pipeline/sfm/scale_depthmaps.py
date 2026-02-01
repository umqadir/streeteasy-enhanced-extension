from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DepthmapScaleEstimate:
    scale_m_per_unit: float
    scale_rel_std: float
    inliers: int
    total: int
    per_image_scales: list[float]
    diagnostics: dict[str, object]


def _robust_mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    return 1.4826 * mad


def estimate_scale_from_depthmaps(
    *,
    depth_units: list[np.ndarray],
    depth_metric_m: list[np.ndarray],
    masks: list[np.ndarray] | None = None,
    max_depth_m: float = 20.0,
    min_pixels_per_image: int = 10_000,
) -> DepthmapScaleEstimate:
    """
    Fits a global scale s (meters per unit) such that:
      depth_metric_m ~= s * depth_units

    Used for DUSt3R/MASt3R outputs (depth is up-to-scale) aligned to a metric depth model.
    """
    if len(depth_units) != len(depth_metric_m):
        raise ValueError("depth_units and depth_metric_m must have same length")
    if masks is not None and len(masks) != len(depth_units):
        raise ValueError("masks must match number of depth maps")

    per_image = []
    weights = []
    total = 0
    inliers_total = 0

    for i, (du, dm) in enumerate(zip(depth_units, depth_metric_m, strict=True)):
        du = np.asarray(du, dtype=np.float64)
        dm = np.asarray(dm, dtype=np.float64)
        if du.shape != dm.shape:
            raise ValueError("depth map shapes must match (align/resize before calling)")
        m = np.isfinite(du) & np.isfinite(dm) & (du > 1e-6) & (dm > 1e-6) & (dm < float(max_depth_m))
        if masks is not None:
            m = m & np.asarray(masks[i], dtype=bool)
        if int(m.sum()) < int(min_pixels_per_image):
            continue

        ratios = (dm[m] / du[m]).astype(np.float64)
        med = float(np.median(ratios))
        mad = _robust_mad(ratios)
        if not np.isfinite(med) or med <= 0:
            continue

        if mad > 0:
            z = np.abs((ratios - med) / mad)
            inlier = z < 3.5
        else:
            inlier = np.ones_like(ratios, dtype=bool)
        if int(inlier.sum()) < int(min_pixels_per_image):
            continue

        s_i = float(np.median(ratios[inlier]))
        per_image.append(s_i)
        weights.append(float(inlier.sum()))
        total += int(ratios.shape[0])
        inliers_total += int(inlier.sum())

    if not per_image:
        raise RuntimeError("No images produced enough valid depth pairs to estimate scale.")

    values = np.asarray(per_image, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    order = np.argsort(values)
    values = values[order]
    w = w[order]
    cum = np.cumsum(w)
    cutoff = 0.5 * float(cum[-1])
    idx = int(np.searchsorted(cum, cutoff))
    scale = float(values[min(idx, values.shape[0] - 1)])
    rel_std = float(_robust_mad(values) / max(scale, 1e-9))

    return DepthmapScaleEstimate(
        scale_m_per_unit=scale,
        scale_rel_std=rel_std,
        inliers=inliers_total,
        total=total,
        per_image_scales=per_image,
        diagnostics={"images_used": len(per_image), "min_pixels_per_image": min_pixels_per_image, "max_depth_m": max_depth_m},
    )

