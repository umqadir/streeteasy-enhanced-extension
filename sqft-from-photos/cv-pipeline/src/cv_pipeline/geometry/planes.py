from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Plane:
    normal: np.ndarray  # (3,) unit
    d: float  # plane equation: normal · x + d = 0

    def signed_distance(self, pts: np.ndarray) -> np.ndarray:
        return pts @ self.normal + float(self.d)


def estimate_gravity_up(rotations_world_to_cam: list[np.ndarray]) -> np.ndarray:
    """
    Approximate gravity 'up' direction by assuming images are mostly upright.
    In COLMAP camera coords: x right, y down, z forward, so 'up' is (0,-1,0).
    World up per image is R^T * up_cam.
    """
    up_cam = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    ups: list[np.ndarray] = []
    for r in rotations_world_to_cam:
        ups.append(r.T @ up_cam)
    v = np.mean(np.stack(ups, axis=0), axis=0)
    n = np.linalg.norm(v)
    if n < 1e-9:
        return np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return v / n


def ransac_plane(
    points: np.ndarray,
    *,
    num_iter: int = 800,
    distance_thresh: float = 0.05,
    rng: np.random.Generator,
) -> tuple[Plane, np.ndarray]:
    if points.shape[0] < 3:
        raise ValueError("Need at least 3 points for plane RANSAC")

    best_inliers: np.ndarray | None = None
    best_plane: Plane | None = None

    n_points = points.shape[0]
    for _ in range(num_iter):
        idx = rng.choice(n_points, size=3, replace=False)
        p0, p1, p2 = points[idx]
        v1 = p1 - p0
        v2 = p2 - p0
        n = np.cross(v1, v2)
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
        raise RuntimeError("Plane RANSAC failed to find a plane")
    return best_plane, best_inliers


def find_floor_plane(
    points: np.ndarray,
    *,
    camera_centers: np.ndarray,
    gravity_up: np.ndarray,
    distance_thresh: float = 0.05,
    rng: np.random.Generator,
) -> tuple[Plane, np.ndarray, dict[str, object]]:
    """
    Returns (plane, inliers, diagnostics). Uses a simple heuristic:
    - prefers planes whose normal aligns with gravity_up
    - prefers planes lying below the cameras (when normal points up)
    """
    if points.shape[0] == 0:
        raise ValueError("No points to find a floor plane")

    # Subsample for speed during candidate search.
    if points.shape[0] > 200_000:
        idx = rng.choice(points.shape[0], size=200_000, replace=False)
        pts = points[idx]
    else:
        pts = points

    best_score = -1.0
    best = None
    best_inliers = None

    for _ in range(6):  # find a few candidate planes, keep best by heuristics
        plane, inliers = ransac_plane(pts, num_iter=600, distance_thresh=distance_thresh, rng=rng)
        n = plane.normal
        align = abs(float(n @ gravity_up))
        # Orient normal to point "up" (roughly).
        if float(n @ gravity_up) < 0:
            n = -n
            plane = Plane(normal=n, d=-plane.d)

        cam_dist = plane.signed_distance(camera_centers)
        frac_cams_above = float((cam_dist > 0).mean())
        support = float(inliers.mean())

        score = support * (0.25 + 0.75 * align) * (0.25 + 0.75 * frac_cams_above)
        if score > best_score:
            best_score = score
            best = plane
            best_inliers = inliers

        # Remove inliers to find another plane.
        pts = pts[~inliers]
        if pts.shape[0] < 10_000:
            break

    if best is None or best_inliers is None:
        raise RuntimeError("Failed to find floor plane candidates")

    inliers_full = np.abs(best.signed_distance(points)) < distance_thresh
    diagnostics = {
        "score": best_score,
        "gravity_align": float(abs(best.normal @ gravity_up)),
        "inlier_ratio": float(inliers_full.mean()),
        "distance_thresh": distance_thresh,
    }
    return best, inliers_full, diagnostics
