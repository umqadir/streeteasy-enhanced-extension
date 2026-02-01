from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import polygonize, unary_union

from cv_pipeline.geometry.planes import Plane

SQFT_PER_M2 = 10.763910416709722


@dataclass(frozen=True)
class FootprintResult:
    area_m2: float
    area_sqft: float
    polygon_wkt: str
    diagnostics: dict[str, object]


def _plane_basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    if abs(float(a @ n)) > 0.9:
        a = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    v /= np.linalg.norm(v)
    return u, v


def project_points_to_plane(points: np.ndarray, plane: Plane) -> np.ndarray:
    u, v = _plane_basis(plane.normal)
    # pick an origin point on the plane: p0 = -d * n
    p0 = -plane.d * plane.normal
    q = points - p0[None, :]
    x = q @ u
    y = q @ v
    return np.stack([x, y], axis=1)


def alpha_shape(points2d: np.ndarray, *, alpha: float) -> Polygon:
    """
    Alpha shape (concave hull) for 2D points.

    alpha: circumradius threshold in the same units as points.
      - smaller => more concave
      - larger => approaches convex hull
    """
    if points2d.shape[0] < 4:
        return MultiPoint(points2d).convex_hull

    tri = Delaunay(points2d)
    triangles = points2d[tri.simplices]

    def circumradius(t: np.ndarray) -> float:
        a = np.linalg.norm(t[0] - t[1])
        b = np.linalg.norm(t[1] - t[2])
        c = np.linalg.norm(t[2] - t[0])
        s = 0.5 * (a + b + c)
        area2 = max(s * (s - a) * (s - b) * (s - c), 0.0)
        if area2 <= 1e-18:
            return float("inf")
        area = np.sqrt(area2)
        return (a * b * c) / (4.0 * area)

    edges: dict[tuple[int, int], int] = {}
    for simplex, t in zip(tri.simplices, triangles, strict=True):
        r = circumradius(t)
        if r > alpha:
            continue
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a_idx = int(simplex[i])
            b_idx = int(simplex[j])
            edge = (a_idx, b_idx) if a_idx < b_idx else (b_idx, a_idx)
            edges[edge] = edges.get(edge, 0) + 1

    boundary = [e for e, count in edges.items() if count == 1]
    if not boundary:
        return MultiPoint(points2d).convex_hull

    lines = [LineString([points2d[i], points2d[j]]) for i, j in boundary]
    polys = list(polygonize(lines))
    if not polys:
        return MultiPoint(points2d).convex_hull
    merged = unary_union(polys)
    if isinstance(merged, Polygon):
        return merged
    # MultiPolygon: return the largest component.
    return max(list(merged.geoms), key=lambda p: p.area)


def auto_alpha(points2d: np.ndarray) -> float:
    if points2d.shape[0] < 10:
        return float("inf")
    tree = cKDTree(points2d)
    d, _ = tree.query(points2d, k=2)
    nn = d[:, 1]
    med = float(np.median(nn))
    return 5.0 * med


def estimate_floor_footprint_sqft(
    points_world: np.ndarray,
    plane: Plane,
    inliers: np.ndarray,
    *,
    alpha: float = 0.0,
) -> FootprintResult:
    floor_pts = points_world[inliers]
    pts2d = project_points_to_plane(floor_pts, plane)

    a = float(alpha) if alpha and alpha > 0 else auto_alpha(pts2d)
    poly = alpha_shape(pts2d, alpha=a)
    area_m2 = float(poly.area)
    return FootprintResult(
        area_m2=area_m2,
        area_sqft=area_m2 * SQFT_PER_M2,
        polygon_wkt=poly.wkt,
        diagnostics={"alpha": a, "floor_points": int(floor_pts.shape[0]), "poly_type": poly.geom_type},
    )

