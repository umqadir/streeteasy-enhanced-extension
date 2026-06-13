// Minimal orient2d for Delaunator. The exact-arithmetic robust-predicates
// package matters only for near-degenerate inputs; our floor point cloud is
// metric-scale and well-conditioned, so the determinant form is sufficient.
export function orient2d(ax, ay, bx, by, cx, cy) {
  return (ay - cy) * (bx - cx) - (ax - cx) * (by - cy);
}
