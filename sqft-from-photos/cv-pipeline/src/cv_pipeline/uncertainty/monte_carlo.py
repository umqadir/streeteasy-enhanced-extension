from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cv_pipeline.geometry.footprint import estimate_floor_footprint_sqft
from cv_pipeline.geometry.planes import find_floor_plane


@dataclass(frozen=True)
class MonteCarloConfig:
    n: int = 200
    scale_rel_std: float = 0.10
    point_subsample_frac: float = 0.5
    alpha: float = 0.0
    max_depth_m: float = 20.0
    distance_thresh: float = 0.06


def _sample_log_normal_factors(rng: np.random.Generator, *, rel_std: float, n: int) -> np.ndarray:
    """
    Returns multiplicative factors with median 1.0.
    Approximate: log(f) ~ Normal(0, rel_std).
    """
    sigma = float(max(1e-6, rel_std))
    return np.exp(rng.normal(loc=0.0, scale=sigma, size=int(n))).astype(np.float64)


def monte_carlo_sqft(
    *,
    points_world_m: np.ndarray,
    camera_centers_m: np.ndarray,
    gravity_up: np.ndarray,
    scale_rel_std: float,
    alpha: float,
    rng: np.random.Generator,
    cfg: MonteCarloConfig | None = None,
) -> tuple[np.ndarray, dict[str, object]]:
    """
    Monte Carlo posterior over sqft using:
    - scale uncertainty (log-normal multiplicative)
    - bootstrap subsampling of points (boundary + plane robustness)
    - re-fitting floor plane each sample
    """
    cfg = cfg or MonteCarloConfig()
    n = int(cfg.n)
    if n <= 0:
        raise ValueError("cfg.n must be > 0")

    pts = np.asarray(points_world_m, dtype=np.float64)
    cams = np.asarray(camera_centers_m, dtype=np.float64)
    up = np.asarray(gravity_up, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points_world_m must be (N,3)")

    factors = _sample_log_normal_factors(rng, rel_std=float(scale_rel_std), n=n)
    out = np.zeros((n,), dtype=np.float64)

    subsample_frac = float(np.clip(cfg.point_subsample_frac, 0.05, 1.0))
    n_pts = int(pts.shape[0])
    m = int(max(1000, subsample_frac * n_pts))
    m = min(m, n_pts)

    for i, f in enumerate(factors):
        pts_i = pts * float(f)
        cams_i = cams * float(f)

        if m < n_pts:
            idx = rng.choice(n_pts, size=m, replace=True)
            pts_s = pts_i[idx]
        else:
            pts_s = pts_i

        try:
            plane, inliers, _plane_diag = find_floor_plane(
                pts_s,
                camera_centers=cams_i,
                gravity_up=up,
                distance_thresh=float(cfg.distance_thresh),
                rng=rng,
            )
            footprint = estimate_floor_footprint_sqft(pts_s, plane, inliers, alpha=float(alpha))
            out[i] = float(footprint.area_sqft)
        except Exception:
            out[i] = np.nan

    valid = np.isfinite(out) & (out > 0)
    diag = {
        "n": n,
        "n_valid": int(valid.sum()),
        "scale_rel_std": float(scale_rel_std),
        "point_subsample_frac": float(subsample_frac),
        "alpha": float(alpha),
    }
    return out[valid], diag

