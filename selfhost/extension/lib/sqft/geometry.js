// Floor-area geometry, ported from estimate_v2b.py (compute_floor_area_single_image
// and helpers). Inputs are a metric 3D point map + normals + validity mask +
// floor segmentation mask; output is visible-floor area in m^2.
import Delaunator from './delaunator.js';

const SQFT_PER_M2 = 10.763910416709722;

// Mulberry32 deterministic RNG (seed 42, matching the Python rng usage spirit).
function makeRng(seed = 42) {
  let s = seed >>> 0;
  return () => {
    s |= 0; s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// RANSAC plane fit. pts: Float64Array flat [x0,y0,z0, x1,y1,z1, ...]; n points.
// Returns {normal:[a,b,c], d, inliers:Uint8Array}.
export function ransacPlane(pts, n, { numIter = 800, distThresh = 0.04, rng = makeRng(42) } = {}) {
  let bestCount = -1, bestNormal = null, bestD = 0, bestInliers = null;
  const px = (i) => pts[i * 3], py = (i) => pts[i * 3 + 1], pz = (i) => pts[i * 3 + 2];
  for (let it = 0; it < numIter; it++) {
    const i0 = (rng() * n) | 0, i1 = (rng() * n) | 0, i2 = (rng() * n) | 0;
    if (i0 === i1 || i1 === i2 || i0 === i2) continue;
    const ax = px(i1) - px(i0), ay = py(i1) - py(i0), az = pz(i1) - pz(i0);
    const bx = px(i2) - px(i0), by = py(i2) - py(i0), bz = pz(i2) - pz(i0);
    let nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
    const norm = Math.hypot(nx, ny, nz);
    if (norm < 1e-9) continue;
    nx /= norm; ny /= norm; nz /= norm;
    const d = -(nx * px(i0) + ny * py(i0) + nz * pz(i0));
    let count = 0;
    for (let i = 0; i < n; i++) {
      const dist = Math.abs(nx * px(i) + ny * py(i) + nz * pz(i) + d);
      if (dist < distThresh) count++;
    }
    if (count > bestCount) {
      bestCount = count; bestNormal = [nx, ny, nz]; bestD = d;
    }
  }
  if (!bestNormal) throw new Error('RANSAC failed');
  const inliers = new Uint8Array(n);
  const [nx, ny, nz] = bestNormal;
  for (let i = 0; i < n; i++) {
    const dist = Math.abs(nx * px(i) + ny * py(i) + nz * pz(i) + bestD);
    inliers[i] = dist < distThresh ? 1 : 0;
  }
  return { normal: bestNormal, d: bestD, inliers };
}

function planeBasis(nrm) {
  let [nx, ny, nz] = nrm;
  const L = Math.hypot(nx, ny, nz); nx /= L; ny /= L; nz /= L;
  let ax = 1, ay = 0, az = 0;
  if (Math.abs(ax * nx + ay * ny + az * nz) > 0.9) { ax = 0; ay = 1; az = 0; }
  let ux = ny * az - nz * ay, uy = nz * ax - nx * az, uz = nx * ay - ny * ax;
  const Lu = Math.hypot(ux, uy, uz); ux /= Lu; uy /= Lu; uz /= Lu;
  let vx = ny * uz - nz * uy, vy = nz * ux - nx * uz, vz = nx * uy - ny * ux;
  const Lv = Math.hypot(vx, vy, vz); vx /= Lv; vy /= Lv; vz /= Lv;
  return { u: [ux, uy, uz], v: [vx, vy, vz], n: [nx, ny, nz] };
}

// Project inlier 3D points to 2D plane coords. Returns Float64Array [x0,y0, x1,y1,...].
export function projectToPlane(pts, n, inliers, plane) {
  const { u, v, n: nn } = planeBasis(plane.normal);
  // p0 = -d * normal
  const p0x = -plane.d * nn[0], p0y = -plane.d * nn[1], p0z = -plane.d * nn[2];
  const out = [];
  for (let i = 0; i < n; i++) {
    if (!inliers[i]) continue;
    const qx = pts[i * 3] - p0x, qy = pts[i * 3 + 1] - p0y, qz = pts[i * 3 + 2] - p0z;
    out.push(qx * u[0] + qy * u[1] + qz * u[2], qx * v[0] + qy * v[1] + qz * v[2]);
  }
  return Float64Array.from(out);
}

// alpha = 5 * median nearest-neighbour distance (matches _auto_alpha).
function autoAlpha(xy, m) {
  if (m < 10) return Infinity;
  // approximate NN via a coarse grid to stay O(n); good enough for alpha scale.
  const dists = new Float64Array(m);
  // brute-force NN is O(m^2); cap sample for speed if huge.
  const step = m > 4000 ? Math.ceil(m / 4000) : 1;
  const sampled = [];
  for (let i = 0; i < m; i += step) {
    let best = Infinity;
    const xi = xy[i * 2], yi = xy[i * 2 + 1];
    for (let j = 0; j < m; j += step) {
      if (i === j) continue;
      const dx = xi - xy[j * 2], dy = yi - xy[j * 2 + 1];
      const d2 = dx * dx + dy * dy;
      if (d2 < best) best = d2;
    }
    sampled.push(Math.sqrt(best));
  }
  sampled.sort((a, b) => a - b);
  return 5.0 * (sampled[(sampled.length / 2) | 0] || 0);
}

function triArea(ax, ay, bx, by, cx, cy) {
  return Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5;
}
function circumradius(ax, ay, bx, by, cx, cy) {
  const a = Math.hypot(ax - bx, ay - by), b = Math.hypot(bx - cx, by - cy), c = Math.hypot(cx - ax, cy - ay);
  const s = 0.5 * (a + b + c);
  const area2 = Math.max(s * (s - a) * (s - b) * (s - c), 0);
  return area2 > 1e-18 ? (a * b * c) / (4 * Math.sqrt(area2)) : Infinity;
}

// Alpha-complex area: sum of Delaunay triangle areas with circumradius <= alpha.
// (Concave-hull area estimate; close to the Python alpha-shape polygon area.)
export function alphaArea(xy) {
  const m = xy.length / 2;
  if (m < 4) return 0;
  const alpha = autoAlpha(xy, m);
  const d = new Delaunator(xy);
  const tris = d.triangles;
  let area = 0, convex = 0;
  for (let t = 0; t < tris.length; t += 3) {
    const i = tris[t], j = tris[t + 1], k = tris[t + 2];
    const ax = xy[2 * i], ay = xy[2 * i + 1];
    const bx = xy[2 * j], by = xy[2 * j + 1];
    const cx = xy[2 * k], cy = xy[2 * k + 1];
    const a = triArea(ax, ay, bx, by, cx, cy);
    convex += a; // Delaunay triangulation covers the convex hull
    if (circumradius(ax, ay, bx, by, cx, cy) <= alpha) area += a;
  }
  return { alphaM2: area, convexM2: convex, alpha };
}

// Full area computation from per-pixel geometry.
// points/normal: Float32Array HWC flat (H*W*3); mask/floor: Uint8Array (H*W).
export function computeFloorArea({ points, normal, mask, floor, H, W,
                                   normalThresh = 0.5, distThresh = 0.04 }) {
  const flat = [];
  for (let p = 0; p < H * W; p++) {
    if (!mask[p] || !floor[p]) continue;
    const ny = normal[p * 3 + 1];
    if (Math.abs(ny) <= normalThresh) continue;
    flat.push(points[p * 3], points[p * 3 + 1], points[p * 3 + 2]);
  }
  const n = flat.length / 3;
  if (n < 100) return { sqft: 0, areaM2: 0, nFloor: n, ok: false };
  const pts = Float64Array.from(flat);
  const plane = ransacPlane(pts, n, { distThresh });
  let nin = 0; for (let i = 0; i < n; i++) nin += plane.inliers[i];
  if (nin < 10) return { sqft: 0, areaM2: 0, nFloor: n, nInliers: nin, ok: false };
  const xy = projectToPlane(pts, n, plane.inliers, plane);
  const { alphaM2, convexM2 } = alphaArea(xy);
  return {
    sqft: alphaM2 * SQFT_PER_M2,
    convexSqft: convexM2 * SQFT_PER_M2,
    areaM2: alphaM2, nFloor: n, nInliers: nin, ok: true,
  };
}
