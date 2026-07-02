// Floor-area geometry, ported from estimate_v2b.py (compute_floor_area_single_image
// and helpers). Inputs are a metric 3D point map + normals + validity mask +
// floor segmentation mask; output is visible-floor area in m^2.
import Delaunator from './delaunator.js';

const SQFT_PER_M2 = 10.763910416709722;
export const ADE20K_WALL = 0;
export const ADE20K_FLOOR = 3;
export const ADE20K_CEILING = 5;
export const ADE20K_FURNITURE = new Set([7, 10, 15, 19, 23, 24, 33, 36, 57]);
const SINGLE_IMAGE_ALPHA_RADIUS_MULTIPLIER = 1.65;

export const LAYOUT_PARAMS = {
  MAX_WALL_POINTS_FOR_RANSAC: 12000,
  MAX_SUPPORT_POINTS_FOR_CHECKS: 30000,
  FLOOR_NORMAL_COS: 0.70,
  WALL_VERTICAL_DOT_MAX: 0.40,
  WALL_H_MIN: 0.25,
  WALL_H_MAX: 3.20,
  FURN_H_MIN: -0.05,
  FURN_H_MAX: 2.20,
  LINE_DIST_M: 0.08,
  LINE_DIST_MIN_M: 0.05,
  LINE_DIST_MAX_M: 0.12,
  RANSAC_ITERS: 2500,
  MIN_WALL_INLIERS: 150,
  MIN_WALL_INLIER_FRAC: 0.01,
  MIN_WALL_LENGTH_M: 0.80,
  MIN_WALL_HEIGHT_SPAN_M: 0.60,
  MIN_NORMAL_AGREEMENT: 0.55,
  MAX_LINES: 8,
  MERGE_ANGLE_DEG: 8.0,
  MERGE_OFFSET_M: 0.15,
  MAX_SNAP_ERR_DEG: 12.0,
  MIN_MANHATTAN_CONC: 0.40,
  GOOD_MANHATTAN_CONC: 0.55,
  SEAM_BIN_PX: 4,
  SEAM_LOOKDOWN_PX: 12,
  SEAM_ASSIGN_DIST_M: 0.25,
  MIN_SEAM_POINTS_FOR_REFINE: 20,
  MAX_SEAM_SHIFT_M: 0.20,
  SUPPORT_CUT_TOL_M: 0.15,
  SUPPORT_EXTENT_TRIM_PCT: 1.0,
  MAX_SUPPORT_VIOLATION_FRAC: 0.03,
  SUPPORT_CONTAIN_BUFFER_M: 0.15,
  MIN_SUPPORT_CONTAIN_HIGH: 0.97,
  MIN_SUPPORT_CONTAIN_LOW: 0.97,
  CAMERA_SIDE_MAX_DIST_M: 1.50,
  CAMERA_WALL_OFFSET_DEFAULT_M: 0.35,
  CAMERA_WALL_OFFSET_LOW_M: 0.15,
  CAMERA_WALL_OFFSET_HIGH_M: 0.75,
  MAX_CAMERA_EXTRAPOLATION_M: 1.00,
  MIN_INFER_MARGIN_M: 0.15,
  SUPPORT_MARGIN_FALLBACK_M: 0.30,
  MIN_ROOM_DIM_M: 1.20,
  MAX_ROOM_DIM_M: 12.00,
  MAX_ROOM_AREA_M2: 80.00,
  MAX_ASPECT_RATIO: 5.00,
  HIGH_CONF_THRESH: 0.70,
  LOW_CONF_THRESH: 0.50,
};

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

function clip01(x) {
  return Math.max(0, Math.min(1, x));
}

function quantileSorted(sorted, q) {
  const n = sorted.length;
  if (!n) return NaN;
  if (n === 1) return sorted[0];
  const pos = (n - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] * (hi - pos) + sorted[hi] * (pos - lo);
}

function quantile(values, q) {
  if (!values.length) return NaN;
  const sorted = Array.from(values).sort((a, b) => a - b);
  return quantileSorted(sorted, q);
}

function median(values) {
  return quantile(values, 0.5);
}

function weightedMedian(values, weights = null) {
  if (!values.length) return NaN;
  if (!weights) return median(values);
  const order = Array.from(values, (v, i) => [v, weights[i]]).sort((a, b) => a[0] - b[0]);
  const cutoff = 0.5 * order.reduce((s, row) => s + row[1], 0);
  let acc = 0;
  for (const [v, w] of order) {
    acc += w;
    if (acc >= cutoff) return v;
  }
  return order[order.length - 1][0];
}

function sampleRows(n, maxN, rng) {
  if (n <= maxN) {
    const out = new Int32Array(n);
    for (let i = 0; i < n; i++) out[i] = i;
    return out;
  }
  const arr = new Int32Array(n);
  for (let i = 0; i < n; i++) arr[i] = i;
  for (let i = 0; i < maxN; i++) {
    const j = i + Math.floor(rng() * (n - i));
    const tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
  }
  return arr.slice(0, maxN);
}

function dot3(a, ax, b) {
  return a[ax] * b[0] + a[ax + 1] * b[1] + a[ax + 2] * b[2];
}

function fitFloorPlaneFromCandidates(pointsFlat, nPoints, { distThresh = 0.04, maxRansacPoints = 200000 } = {}) {
  const rng = makeRng(42);
  if (nPoints < 100) throw new Error('Not enough floor points for plane fitting');
  let ransacPts = pointsFlat;
  let ransacN = nPoints;
  if (nPoints > maxRansacPoints) {
    const idx = sampleRows(nPoints, maxRansacPoints, rng);
    const sampled = new Float64Array(maxRansacPoints * 3);
    for (let i = 0; i < maxRansacPoints; i++) {
      const src = idx[i] * 3, dst = i * 3;
      sampled[dst] = pointsFlat[src];
      sampled[dst + 1] = pointsFlat[src + 1];
      sampled[dst + 2] = pointsFlat[src + 2];
    }
    ransacPts = sampled;
    ransacN = maxRansacPoints;
  }
  const plane0 = ransacPlane(ransacPts, ransacN, { distThresh, rng });
  const [nx, ny, nz] = plane0.normal;
  const d = plane0.d;
  const inliers = new Uint8Array(nPoints);
  let nIn = 0;
  let sumSq = 0;
  for (let i = 0; i < nPoints; i++) {
    const off = i * 3;
    const dist = Math.abs(nx * pointsFlat[off] + ny * pointsFlat[off + 1] + nz * pointsFlat[off + 2] + d);
    if (dist < distThresh) {
      inliers[i] = 1;
      nIn++;
      sumSq += dist * dist;
    }
  }
  return {
    plane: { normal: [nx, ny, nz], d },
    floorPoints: pointsFlat,
    nCandidates: nPoints,
    inliers,
    nInliers: nIn,
    residualRmsM: nIn ? Math.sqrt(sumSq / nIn) : Infinity,
  };
}

function collectFloorCandidates({ points, normal, mask, seg, H, W }, normalThresh = 0.5) {
  const flat = [];
  const total = H * W;
  for (let p = 0; p < total; p++) {
    if (!mask[p] || seg[p] !== ADE20K_FLOOR) continue;
    if (Math.abs(normal[p * 3 + 1]) <= normalThresh) continue;
    flat.push(points[p * 3], points[p * 3 + 1], points[p * 3 + 2]);
  }
  return Float64Array.from(flat);
}

function signedDistances(pointsFlat, n, plane) {
  const [nx, ny, nz] = plane.normal;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const off = i * 3;
    out[i] = Math.abs(nx * pointsFlat[off] + ny * pointsFlat[off + 1] + nz * pointsFlat[off + 2] + plane.d);
  }
  return out;
}

function visibleAreaFromFit(fit) {
  if (fit.nInliers < 10) return 0;
  const xy = projectToPlane(fit.floorPoints, fit.nCandidates, fit.inliers, fit.plane);
  const area = alphaArea(xy);
  return (area.alphaM2 || 0) * SQFT_PER_M2;
}

function orientFloorPlane(plane) {
  let n = [...plane.normal];
  const L = Math.hypot(n[0], n[1], n[2]);
  n = [n[0] / L, n[1] / L, n[2] / L];
  let d = plane.d;
  let p0 = [-d * n[0], -d * n[1], -d * n[2]];
  if (n[0] * (-p0[0]) + n[1] * (-p0[1]) + n[2] * (-p0[2]) < 0) {
    n = [-n[0], -n[1], -n[2]];
    d = -d;
    p0 = [-d * n[0], -d * n[1], -d * n[2]];
  }
  const cameraHeight = (-p0[0]) * n[0] + (-p0[1]) * n[1] + (-p0[2]) * n[2];
  return { nF: n, d, p0, cameraHeight };
}

function preliminaryFloorBasis(nF) {
  let tmp = [0, 1, 0];
  if (Math.abs(tmp[0] * nF[0] + tmp[1] * nF[1] + tmp[2] * nF[2]) > 0.9) tmp = [1, 0, 0];
  const dot = tmp[0] * nF[0] + tmp[1] * nF[1] + tmp[2] * nF[2];
  let b0 = [tmp[0] - dot * nF[0], tmp[1] - dot * nF[1], tmp[2] - dot * nF[2]];
  let L = Math.hypot(b0[0], b0[1], b0[2]);
  b0 = [b0[0] / L, b0[1] / L, b0[2] / L];
  let b1 = [
    nF[1] * b0[2] - nF[2] * b0[1],
    nF[2] * b0[0] - nF[0] * b0[2],
    nF[0] * b0[1] - nF[1] * b0[0],
  ];
  L = Math.hypot(b1[0], b1[1], b1[2]);
  b1 = [b1[0] / L, b1[1] / L, b1[2] / L];
  return { b0, b1 };
}

function projectOnePoint(pt, nF, p0, b0, b1) {
  const vx = pt[0] - p0[0], vy = pt[1] - p0[1], vz = pt[2] - p0[2];
  const h = vx * nF[0] + vy * nF[1] + vz * nF[2];
  const px = pt[0] - h * nF[0] - p0[0];
  const py = pt[1] - h * nF[1] - p0[1];
  const pz = pt[2] - h * nF[2] - p0[2];
  return { qx: px * b0[0] + py * b0[1] + pz * b0[2], qy: px * b1[0] + py * b1[1] + pz * b1[2], h };
}

function projectFlatPoints(points, indices, nF, p0, b0, b1) {
  const q = new Float64Array(indices.length * 2);
  const h = new Float64Array(indices.length);
  for (let i = 0; i < indices.length; i++) {
    const off = indices[i] * 3;
    const out = projectOnePoint([points[off], points[off + 1], points[off + 2]], nF, p0, b0, b1);
    q[i * 2] = out.qx; q[i * 2 + 1] = out.qy; h[i] = out.h;
  }
  return { q, h };
}

function projectNormalsToFloor(normal, indices, nF, b0, b1) {
  const out = new Float64Array(indices.length * 2);
  for (let i = 0; i < indices.length; i++) {
    const off = indices[i] * 3;
    const nd = normal[off] * nF[0] + normal[off + 1] * nF[1] + normal[off + 2] * nF[2];
    let px = normal[off] - nd * nF[0];
    let py = normal[off + 1] - nd * nF[1];
    let pz = normal[off + 2] - nd * nF[2];
    let L = Math.hypot(px, py, pz);
    if (L > 1e-6) { px /= L; py /= L; pz /= L; }
    let nx = px * b0[0] + py * b0[1] + pz * b0[2];
    let ny = px * b1[0] + py * b1[1] + pz * b1[2];
    L = Math.hypot(nx, ny);
    if (L > 1e-6) { nx /= L; ny /= L; }
    out[i * 2] = nx; out[i * 2 + 1] = ny;
  }
  return out;
}

function compactQH(q, h, keep) {
  const n = keep.reduce((s, v) => s + (v ? 1 : 0), 0);
  const q2 = new Float64Array(n * 2);
  const h2 = new Float64Array(n);
  const map = new Int32Array(n);
  let j = 0;
  for (let i = 0; i < keep.length; i++) {
    if (!keep[i]) continue;
    q2[j * 2] = q[i * 2]; q2[j * 2 + 1] = q[i * 2 + 1]; h2[j] = h[i]; map[j] = i; j++;
  }
  return { q: q2, h: h2, map };
}

function lineFromTwoPoints(q, ia, ib) {
  const ax = q[ia * 2], ay = q[ia * 2 + 1], bx = q[ib * 2], by = q[ib * 2 + 1];
  let tx = bx - ax, ty = by - ay;
  const L = Math.hypot(tx, ty);
  if (L < 1e-6) return null;
  tx /= L; ty /= L;
  const nx = -ty, ny = tx;
  const c = -(nx * ax + ny * ay);
  return { normal: [nx, ny], c, tangent: [tx, ty] };
}

function lineStats(qWall, wallNormals2d, wallHeights, normal2, c, tangent, lineDistM) {
  const inliers = [];
  const along = [];
  const heights = [];
  const dists = [];
  let normalAgreeSum = 0;
  const n = qWall.length / 2;
  for (let i = 0; i < n; i++) {
    const dist = Math.abs(qWall[i * 2] * normal2[0] + qWall[i * 2 + 1] * normal2[1] + c);
    if (dist >= lineDistM) continue;
    inliers.push(i);
    along.push(qWall[i * 2] * tangent[0] + qWall[i * 2 + 1] * tangent[1]);
    heights.push(wallHeights[i]);
    normalAgreeSum += Math.abs(wallNormals2d[i * 2] * normal2[0] + wallNormals2d[i * 2 + 1] * normal2[1]);
    dists.push(dist);
  }
  if (!inliers.length) return { inlierIdx: new Int32Array(0), spanM: 0, heightSpanM: 0, meanNormalAgreement: 0, medianDistM: Infinity };
  const hMin = Math.min(...heights), hMax = Math.max(...heights);
  return {
    inlierIdx: Int32Array.from(inliers),
    spanM: quantile(along, 0.98) - quantile(along, 0.02),
    heightSpanM: hMax - hMin,
    meanNormalAgreement: normalAgreeSum / inliers.length,
    medianDistM: median(dists),
  };
}

function directionRad(line) {
  let a = Math.atan2(line.tangent[1], line.tangent[0]) % Math.PI;
  if (a < 0) a += Math.PI;
  return a;
}

function angleDiffModPi(a, b) {
  return Math.abs((((a - b + Math.PI / 2) % Math.PI) + Math.PI) % Math.PI - Math.PI / 2);
}

function mergeDuplicateLines(lines, qWall, wallNormals2d, wallHeights, params) {
  if (!lines.length) return [];
  const used = new Uint8Array(lines.length);
  const merged = [];
  const angleTol = params.MERGE_ANGLE_DEG * Math.PI / 180;
  const offsetTol = params.MERGE_OFFSET_M;
  for (let i = 0; i < lines.length; i++) {
    if (used[i]) continue;
    const group = [i];
    used[i] = 1;
    const thetaI = directionRad(lines[i]);
    for (let j = i + 1; j < lines.length; j++) {
      if (used[j]) continue;
      if (angleDiffModPi(thetaI, directionRad(lines[j])) > angleTol) continue;
      let cj = lines[j].c;
      if (lines[i].normal[0] * lines[j].normal[0] + lines[i].normal[1] * lines[j].normal[1] < 0) cj = -cj;
      if (Math.abs(lines[i].c - cj) <= offsetTol) {
        group.push(j);
        used[j] = 1;
      }
    }
    if (group.length === 1) {
      merged.push(lines[i]);
      continue;
    }
    const weights = group.map(k => lines[k].score);
    let sin2 = 0, cos2 = 0;
    for (let g = 0; g < group.length; g++) {
      const th = directionRad(lines[group[g]]);
      sin2 += weights[g] * Math.sin(2 * th);
      cos2 += weights[g] * Math.cos(2 * th);
    }
    const theta = 0.5 * Math.atan2(sin2, cos2);
    const t = [Math.cos(theta), Math.sin(theta)];
    const n2 = [-t[1], t[0]];
    const offsets = [];
    for (const k of group) {
      let ck = lines[k].c;
      if (n2[0] * lines[k].normal[0] + n2[1] * lines[k].normal[1] < 0) ck = -ck;
      offsets.push(ck);
    }
    const c = weightedMedian(offsets, weights);
    const stats = lineStats(qWall, wallNormals2d, wallHeights, n2, c, t, params.LINE_DIST_M);
    merged.push({ normal: n2, c, tangent: t, ...stats, score: weights.reduce((s, v) => s + v, 0) });
  }
  return merged;
}

function ransacWallLines(qWall, wallNormals2d, wallHeights, params) {
  const nWall = qWall.length / 2;
  if (nWall < 2) return [];
  const rng = makeRng(42);
  const sampleIdx = sampleRows(nWall, params.MAX_WALL_POINTS_FOR_RANSAC, rng);
  const remaining = new Uint8Array(sampleIdx.length);
  remaining.fill(1);
  const minInliers = Math.max(params.MIN_WALL_INLIERS, Math.floor(params.MIN_WALL_INLIER_FRAC * nWall));
  const lines = [];
  for (let lineNo = 0; lineNo < params.MAX_LINES; lineNo++) {
    const rem = [];
    for (let i = 0; i < remaining.length; i++) if (remaining[i]) rem.push(i);
    if (rem.length < minInliers) break;
    let best = null;
    for (let iter = 0; iter < params.RANSAC_ITERS; iter++) {
      const iaLocal = Math.floor(rng() * rem.length);
      let ibLocal = Math.floor(rng() * (rem.length - 1));
      if (ibLocal >= iaLocal) ibLocal++;
      const ia = sampleIdx[rem[iaLocal]], ib = sampleIdx[rem[ibLocal]];
      const line = lineFromTwoPoints(qWall, ia, ib);
      if (!line) continue;
      const { normal: n2, c, tangent: t2 } = line;
      let count = 0, scoreWeight = 0, normalAgree = 0;
      const along = [], heights = [];
      for (const r of rem) {
        const idx = sampleIdx[r];
        const dist = Math.abs(qWall[idx * 2] * n2[0] + qWall[idx * 2 + 1] * n2[1] + c);
        if (dist >= params.LINE_DIST_M) continue;
        count++;
        scoreWeight += clip01(1 - dist / params.LINE_DIST_M);
        normalAgree += Math.abs(wallNormals2d[idx * 2] * n2[0] + wallNormals2d[idx * 2 + 1] * n2[1]);
        along.push(qWall[idx * 2] * t2[0] + qWall[idx * 2 + 1] * t2[1]);
        heights.push(wallHeights[idx]);
      }
      if (count < Math.max(100, Math.floor(minInliers / 3))) continue;
      const span = quantile(along, 0.98) - quantile(along, 0.02);
      const heightSpan = Math.max(...heights) - Math.min(...heights);
      const agreement = normalAgree / count;
      const score = scoreWeight * agreement * Math.min(1.5, Math.max(0.25, span / 2));
      if (!best || score > best.score) best = { score, normal: n2, c, tangent: t2, span, heightSpan, agreement };
    }
    if (!best) break;
    const stats = lineStats(qWall, wallNormals2d, wallHeights, best.normal, best.c, best.tangent, params.LINE_DIST_M);
    if (!(
      stats.inlierIdx.length >= minInliers
      && stats.spanM >= params.MIN_WALL_LENGTH_M
      && stats.heightSpanM >= params.MIN_WALL_HEIGHT_SPAN_M
      && stats.meanNormalAgreement >= params.MIN_NORMAL_AGREEMENT
      && stats.medianDistM <= params.LINE_DIST_M
    )) break;
    lines.push({ normal: best.normal, c: best.c, tangent: best.tangent, ...stats, score: best.score });
    for (const r of rem) {
      const idx = sampleIdx[r];
      if (Math.abs(qWall[idx * 2] * best.normal[0] + qWall[idx * 2 + 1] * best.normal[1] + best.c) < 1.5 * params.LINE_DIST_M) {
        remaining[r] = 0;
      }
    }
  }
  return mergeDuplicateLines(lines, qWall, wallNormals2d, wallHeights, params);
}

function extractSeamPoints(points, valid, seg, H, W, nF, p0, b0, b1, params) {
  const total = H * W;
  const seam = new Uint8Array(total);
  const lookdown = params.SEAM_LOOKDOWN_PX;
  const binPx = params.SEAM_BIN_PX;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const p = y * W + x;
      if (seg[p] !== ADE20K_WALL) continue;
      let below = false;
      for (let k = 1; k <= lookdown && y + k < H; k++) {
        const p2 = (y + k) * W + x;
        if (seg[p2] === ADE20K_FLOOR || ADE20K_FURNITURE.has(seg[p2]) || seg[p2] !== ADE20K_WALL) {
          below = true; break;
        }
      }
      if (below) seam[p] = 1;
    }
  }
  for (let x0 = 0; x0 < W; x0 += binPx) {
    const x1 = Math.min(W, x0 + binPx);
    for (let y = H - 2; y >= 0; y--) {
      let wallAny = false;
      for (let x = x0; x < x1; x++) {
        if (seg[y * W + x] === ADE20K_WALL) { wallAny = true; break; }
      }
      if (!wallAny) continue;
      let supportBelow = false;
      for (let yy = y + 1; yy < Math.min(H, y + lookdown + 1) && !supportBelow; yy++) {
        for (let x = x0; x < x1; x++) {
          const p = yy * W + x;
          if (seg[p] === ADE20K_FLOOR || ADE20K_FURNITURE.has(seg[p]) || seg[p] !== ADE20K_WALL) {
            supportBelow = true; break;
          }
        }
      }
      if (!supportBelow) continue;
      for (let yy = Math.max(0, y - 2); yy < Math.min(H, y + 3); yy++) {
        for (let x = x0; x < x1; x++) {
          const p = yy * W + x;
          if (seg[p] === ADE20K_WALL) seam[p] = 1;
        }
      }
      break;
    }
  }
  const out = [];
  for (let p = 0; p < total; p++) {
    if (!seam[p]) continue;
    let src = -1;
    if (valid[p]) {
      src = p;
    } else {
      const y = Math.floor(p / W), x = p - y * W;
      for (let yy = Math.max(0, y - 10); yy < y; yy++) {
        const pp = yy * W + x;
        if (valid[pp] && seg[pp] === ADE20K_WALL) { src = pp; break; }
      }
    }
    if (src < 0) continue;
    const off = src * 3;
    const proj = projectOnePoint([points[off], points[off + 1], points[off + 2]], nF, p0, b0, b1);
    if (proj.h > 0.15 && proj.h < params.WALL_H_MAX) out.push(proj.qx, proj.qy);
  }
  return Float64Array.from(out);
}

function manhattanFrame(lines) {
  if (!lines.length) return { theta0: 0, concentration: 0 };
  let zr = 0, zi = 0, wsum = 0;
  for (const line of lines) {
    const theta = directionRad(line);
    const w = line.inlierIdx.length * line.meanNormalAgreement * Math.min(1.5, Math.max(0.25, line.spanM / 2));
    zr += w * Math.cos(4 * theta);
    zi += w * Math.sin(4 * theta);
    wsum += w;
  }
  if (wsum <= 0) return { theta0: 0, concentration: 0 };
  let theta0 = (Math.atan2(zi, zr) / 4) % (Math.PI / 2);
  if (theta0 < 0) theta0 += Math.PI / 2;
  return { theta0, concentration: Math.hypot(zr, zi) / wsum };
}

function toManhattan(q, theta0) {
  const out = new Float64Array(q.length);
  const cx = Math.cos(theta0), sx = Math.sin(theta0);
  for (let i = 0; i < q.length / 2; i++) {
    const x = q[i * 2], y = q[i * 2 + 1];
    out[i * 2] = x * cx + y * sx;
    out[i * 2 + 1] = -x * sx + y * cx;
  }
  return out;
}

function supportViolation(side, c, rSupport, tol) {
  const n = rSupport.length / 2;
  let bad = 0;
  for (let i = 0; i < n; i++) {
    const x = rSupport[i * 2], y = rSupport[i * 2 + 1];
    if ((side === 'x_min' && x < c - tol)
      || (side === 'x_max' && x > c + tol)
      || (side === 'y_min' && y < c - tol)
      || (side === 'y_max' && y > c + tol)) bad++;
  }
  return n ? bad / n : 1;
}

function wallConfidence(line, minWallInliers, lineDistM) {
  const inlierCountScore = clip01(line.inlierIdx.length / (3 * minWallInliers));
  const wallSpanScore = clip01(line.spanM / 2.5);
  const normalScore = clip01((line.meanNormalAgreement - 0.5) / 0.4);
  const residualScore = clip01(1 - line.medianDistM / lineDistM);
  return clip01(0.35 * inlierCountScore + 0.25 * wallSpanScore + 0.20 * normalScore + 0.20 * residualScore);
}

function boundaryDict(c) {
  return {
    c_m: c.c,
    source: c.source,
    confidence: c.confidence,
    span_m: c.spanM,
    support_violation_frac: c.supportViolationFrac,
    n_points: c.nPoints,
    snap_error_deg: c.snapErrorDeg || 0,
    median_residual_m: c.medianResidualM || 0,
    normal_agreement: c.normalAgreement || 0,
    seam_points: c.seamPoints || 0,
    inferred: !!c.inferred,
    lower_bound: !!c.lowerBound,
  };
}

function boundaryFromWallLine(lineId, line, rWall, rSupport, rSeam, theta0, minWallInliers, params) {
  const axisX = [Math.cos(theta0), Math.sin(theta0)];
  const axisY = [-Math.sin(theta0), Math.cos(theta0)];
  const alignX = Math.abs(line.tangent[0] * axisX[0] + line.tangent[1] * axisX[1]);
  const alignY = Math.abs(line.tangent[0] * axisY[0] + line.tangent[1] * axisY[1]);
  const snap = Math.acos(Math.max(-1, Math.min(1, Math.max(alignX, alignY)))) * 180 / Math.PI;
  if (snap > params.MAX_SNAP_ERR_DEG) return null;
  const rinX = [], rinY = [];
  for (const idx of line.inlierIdx) {
    rinX.push(rWall[idx * 2]);
    rinY.push(rWall[idx * 2 + 1]);
  }
  let c0, interval, span, side, seamAxis, wallAxis;
  const near = [];
  if (alignY > alignX) {
    c0 = weightedMedian(rinX);
    interval = [quantile(rinY, 0.05), quantile(rinY, 0.95)];
    span = interval[1] - interval[0];
    for (let i = 0; i < rSupport.length / 2; i++) {
      const x = rSupport[i * 2], y = rSupport[i * 2 + 1];
      if (y >= interval[0] - 0.75 && y <= interval[1] + 0.75 && Math.abs(x - c0) < 2.5) near.push(x - c0);
    }
    if (!near.length) for (let i = 0; i < rSupport.length / 2; i++) near.push(rSupport[i * 2] - c0);
    side = median(near) > 0 ? 'x_min' : 'x_max';
    seamAxis = [];
    for (let i = 0; i < rSeam.length / 2; i++) seamAxis.push(rSeam[i * 2]);
    wallAxis = rinX;
  } else {
    c0 = weightedMedian(rinY);
    interval = [quantile(rinX, 0.05), quantile(rinX, 0.95)];
    span = interval[1] - interval[0];
    for (let i = 0; i < rSupport.length / 2; i++) {
      const x = rSupport[i * 2], y = rSupport[i * 2 + 1];
      if (x >= interval[0] - 0.75 && x <= interval[1] + 0.75 && Math.abs(y - c0) < 2.5) near.push(y - c0);
    }
    if (!near.length) for (let i = 0; i < rSupport.length / 2; i++) near.push(rSupport[i * 2 + 1] - c0);
    side = median(near) > 0 ? 'y_min' : 'y_max';
    seamAxis = [];
    for (let i = 0; i < rSeam.length / 2; i++) seamAxis.push(rSeam[i * 2 + 1]);
    wallAxis = rinY;
  }
  let seamCount = 0;
  let c = c0;
  if (seamAxis.length) {
    const seamNear = seamAxis.filter(v => Math.abs(v - c0) < params.SEAM_ASSIGN_DIST_M);
    seamCount = seamNear.length;
    if (seamCount >= params.MIN_SEAM_POINTS_FOR_REFINE) {
      const vals = wallAxis.concat(seamNear);
      const weights = Array(wallAxis.length).fill(1).concat(Array(seamNear.length).fill(2));
      const cRefined = weightedMedian(vals, weights);
      if (Math.abs(cRefined - c0) <= params.MAX_SEAM_SHIFT_M) c = cRefined;
    }
  }
  const violation = supportViolation(side, c, rSupport, params.SUPPORT_CUT_TOL_M);
  if (violation > params.MAX_SUPPORT_VIOLATION_FRAC) return null;
  const confidence = wallConfidence(line, minWallInliers, params.LINE_DIST_M);
  return {
    side, c,
    source: seamCount >= params.MIN_SEAM_POINTS_FOR_REFINE ? 'wall_plane+seam' : 'wall_plane',
    confidence,
    spanM: span,
    supportViolationFrac: violation,
    nPoints: line.inlierIdx.length,
    snapErrorDeg: snap,
    medianResidualM: line.medianDistM,
    normalAgreement: line.meanNormalAgreement,
    seamPoints: seamCount,
    lineId,
  };
}

function seamOnlyCandidates(rSeam, rSupport, params) {
  if (rSeam.length / 2 < params.MIN_SEAM_POINTS_FOR_REFINE) return [];
  const sx = [], sy = [];
  for (let i = 0; i < rSupport.length / 2; i++) { sx.push(rSupport[i * 2]); sy.push(rSupport[i * 2 + 1]); }
  const supportQ = {
    x_min: quantile(sx, 0.01), x_max: quantile(sx, 0.99),
    y_min: quantile(sy, 0.01), y_max: quantile(sy, 0.99),
  };
  const specs = [['x_min', 0, 1, 0.01], ['x_max', 0, 1, 0.99], ['y_min', 1, 0, 0.01], ['y_max', 1, 0, 0.99]];
  const out = [];
  for (const [side, axis, otherAxis, qtile] of specs) {
    const vals = [];
    for (let i = 0; i < rSeam.length / 2; i++) vals.push(rSeam[i * 2 + axis]);
    const c = quantile(vals, qtile);
    const nearOther = [];
    for (let i = 0; i < rSeam.length / 2; i++) {
      if (Math.abs(rSeam[i * 2 + axis] - c) < params.SEAM_ASSIGN_DIST_M) nearOther.push(rSeam[i * 2 + otherAxis]);
    }
    if (nearOther.length < params.MIN_SEAM_POINTS_FOR_REFINE) continue;
    if (Math.abs(c - supportQ[side]) > 0.60) continue;
    const span = quantile(nearOther, 0.95) - quantile(nearOther, 0.05);
    const minSeamSpan = Math.min(0.25, params.MIN_WALL_LENGTH_M);
    if (span < minSeamSpan) continue;
    const violation = supportViolation(side, c, rSupport, params.SUPPORT_CUT_TOL_M);
    if (violation > params.MAX_SUPPORT_VIOLATION_FRAC) continue;
    const confidence = clip01(0.28 + 0.35 * clip01(nearOther.length / 400) + 0.25 * clip01(span / 2.5) - violation);
    out.push({ side, c, source: 'seam', confidence, spanM: span, supportViolationFrac: violation, nPoints: nearOther.length, seamPoints: nearOther.length });
  }
  return out;
}

function selectSides(candidates, rSupport) {
  const sx = [], sy = [];
  for (let i = 0; i < rSupport.length / 2; i++) { sx.push(rSupport[i * 2]); sy.push(rSupport[i * 2 + 1]); }
  const supportExtent = { x_min: quantile(sx, 0.01), x_max: quantile(sx, 0.99), y_min: quantile(sy, 0.01), y_max: quantile(sy, 0.99) };
  const selected = {};
  for (const side of ['x_min', 'x_max', 'y_min', 'y_max']) {
    const sideCandidates = candidates.filter(c => c.side === side);
    if (!sideCandidates.length) continue;
    let best = null, bestScore = -Infinity;
    for (const cand of sideCandidates) {
      const outsideGap = side.endsWith('min') ? supportExtent[side] - cand.c : cand.c - supportExtent[side];
      const spanScore = clip01(cand.spanM / 2.5);
      const seamScore = clip01((cand.seamPoints || 0) / 250);
      const score = cand.confidence + 0.35 * seamScore + 0.20 * spanScore - 0.15 * Math.max(0, outsideGap - 1.5) - cand.supportViolationFrac;
      if (score > bestScore) { best = cand; bestScore = score; }
    }
    selected[side] = best;
  }
  return selected;
}

function inferMissingSides(selected, rSupport, params) {
  const missing = ['x_min', 'x_max', 'y_min', 'y_max'].filter(side => !selected[side]);
  const info = { num_inferred: 0, area_sqft_low: null, area_sqft_mid: null, area_sqft_high: null };
  if (missing.length !== 1) return { selected, info };
  if (Object.values(selected).filter(c => !c.inferred).length !== 3) return { selected, info };
  const side = missing[0], axis = side.startsWith('x') ? 0 : 1, otherAxis = 1 - axis;
  const trimQ = params.SUPPORT_EXTENT_TRIM_PCT / 100;
  const vals = [], otherVals = [];
  for (let i = 0; i < rSupport.length / 2; i++) { vals.push(rSupport[i * 2 + axis]); otherVals.push(rSupport[i * 2 + otherAxis]); }
  const cMid = side.endsWith('min') ? quantile(vals, trimQ) : quantile(vals, 1 - trimQ);
  const otherMin = quantile(otherVals, trimQ), otherMax = quantile(otherVals, 1 - trimQ);
  const violation = supportViolation(side, cMid, rSupport, params.SUPPORT_CUT_TOL_M);
  selected[side] = {
    side, c: cMid, source: 'inferred_support_extent', confidence: 0.38,
    spanM: otherMax - otherMin, supportViolationFrac: violation, nPoints: 0,
    inferred: true, lowerBound: true,
  };
  Object.assign(info, { num_inferred: 1, missing_side: side, inferred_side: side, inferred_lower_bound: true, support_side_c: cMid, c_mid: cMid });
  return { selected, info };
}

function roomDimensions(selected) {
  const width = selected.x_max.c - selected.x_min.c;
  const depth = selected.y_max.c - selected.y_min.c;
  return { width, depth, area: width * depth };
}

function dimensionGate(width, depth, area, params) {
  if (width < params.MIN_ROOM_DIM_M || depth < params.MIN_ROOM_DIM_M) return [false, 'room_dimension_too_small'];
  if (width > params.MAX_ROOM_DIM_M || depth > params.MAX_ROOM_DIM_M) return [false, 'room_dimension_too_large'];
  if (area > params.MAX_ROOM_AREA_M2) return [false, 'room_area_too_large'];
  if (Math.max(width, depth) / Math.min(width, depth) > params.MAX_ASPECT_RATIO) return [false, 'room_aspect_ratio_too_large'];
  return [true, null];
}

function supportContainment(selected, rSupport, bufferM) {
  const xMin = selected.x_min.c - bufferM, xMax = selected.x_max.c + bufferM;
  const yMin = selected.y_min.c - bufferM, yMax = selected.y_max.c + bufferM;
  const n = rSupport.length / 2;
  let inside = 0;
  for (let i = 0; i < n; i++) {
    const x = rSupport[i * 2], y = rSupport[i * 2 + 1];
    if (x >= xMin && x <= xMax && y >= yMin && y <= yMax) inside++;
  }
  return n ? inside / n : 0;
}

function visibleOnly(reason, visibleSqft, visibleM2, logs) {
  logs.reason = reason;
  return {
    status: 'visible_only',
    areaSqft: visibleSqft,
    visibleSqft,
    confidence: 0,
    boundaries: {},
    roomDimsM: null,
    areaM2: visibleM2,
    logs,
  };
}

// Wall-to-wall layout completion ported from /tmp/layout/layout_area.py.
export function completeLayout({ points, normal, mask, seg, H, W }) {
  const params = { ...LAYOUT_PARAMS };
  const floorCandidates = collectFloorCandidates({ points, normal, mask, seg, H, W });
  let floorFit;
  try {
    floorFit = fitFloorPlaneFromCandidates(floorCandidates, floorCandidates.length / 3);
  } catch (err) {
    return { status: 'visible_only', areaSqft: 0, visibleSqft: 0, confidence: 0, boundaries: {}, roomDimsM: null, logs: { reason: String(err?.message || err) } };
  }
  const visibleSqft = visibleAreaFromFit(floorFit);
  const visibleM2 = visibleSqft / SQFT_PER_M2;
  const dists = signedDistances(floorFit.floorPoints, floorFit.nCandidates, floorFit.plane);
  const floorResidMedian = quantile(dists, 0.5);
  const floorResidP95 = quantile(dists, 0.95);
  const { nF, p0, cameraHeight } = orientFloorPlane(floorFit.plane);
  const { b0, b1 } = preliminaryFloorBasis(nF);
  const logs = {
    visible_area_sqft: visibleSqft,
    camera_height_m: cameraHeight,
    floor_plane_residual_median_m: floorResidMedian,
    floor_plane_residual_p95_m: floorResidP95,
    floor_plane_residual_rms_m: floorFit.residualRmsM,
  };
  if (floorResidMedian > 0.07 || floorResidP95 > 0.18) return visibleOnly('floor_plane_residual_too_high', visibleSqft, visibleM2, logs);
  if (cameraHeight < 0.5 || cameraHeight > 2.5) return visibleOnly('camera_height_outside_gate', visibleSqft, visibleM2, logs);

  const floorIdx = [], wallIdx = [], furnIdx = [];
  const total = H * W;
  for (let p = 0; p < total; p++) {
    if (!mask[p]) continue;
    const cls = seg[p];
    const off = p * 3;
    const nd = normal[off] * nF[0] + normal[off + 1] * nF[1] + normal[off + 2] * nF[2];
    if (cls === ADE20K_FLOOR && Math.abs(nd) > params.FLOOR_NORMAL_COS) floorIdx.push(p);
    if (cls === ADE20K_WALL && Math.abs(nd) < params.WALL_VERTICAL_DOT_MAX) wallIdx.push(p);
    if (ADE20K_FURNITURE.has(cls)) furnIdx.push(p);
  }
  const floorProj = projectFlatPoints(points, floorIdx, nF, p0, b0, b1);
  const wallProj0 = projectFlatPoints(points, wallIdx, nF, p0, b0, b1);
  const wallKeep = Array.from(wallProj0.h, h => h > params.WALL_H_MIN && h < params.WALL_H_MAX);
  const wallCompact = compactQH(wallProj0.q, wallProj0.h, wallKeep);
  const keptWallIdx = Array.from(wallCompact.map, i => wallIdx[i]);
  const wallNormals2d = projectNormalsToFloor(normal, keptWallIdx, nF, b0, b1);
  const furnProj0 = projectFlatPoints(points, furnIdx, nF, p0, b0, b1);
  const furnKeep = Array.from(furnProj0.h, h => h > params.FURN_H_MIN && h < params.FURN_H_MAX);
  const furnCompact = compactQH(furnProj0.q, furnProj0.h, furnKeep);
  const qSupport = new Float64Array(floorProj.q.length + furnCompact.q.length);
  qSupport.set(floorProj.q, 0);
  qSupport.set(furnCompact.q, floorProj.q.length);
  if (qSupport.length / 2 < 100) return visibleOnly('too_few_support_points', visibleSqft, visibleM2, logs);

  const minWallInliers = Math.max(params.MIN_WALL_INLIERS, Math.floor(params.MIN_WALL_INLIER_FRAC * (wallCompact.q.length / 2)));
  const rawLines = ransacWallLines(wallCompact.q, wallNormals2d, wallCompact.h, params);
  const qSeam = extractSeamPoints(points, mask, seg, H, W, nF, p0, b0, b1, params);
  const { theta0, concentration } = manhattanFrame(rawLines);
  const rWall = wallCompact.q.length ? toManhattan(wallCompact.q, theta0) : new Float64Array(0);
  const rSupport = toManhattan(qSupport, theta0);
  const rSeam = qSeam.length ? toManhattan(qSeam, theta0) : new Float64Array(0);
  const camProj = projectOnePoint([0, 0, 0], nF, p0, b0, b1);
  const rCamArr = toManhattan(Float64Array.from([camProj.qx, camProj.qy]), theta0);
  logs.num_wall_points = wallCompact.q.length / 2;
  logs.num_floor_points = floorProj.q.length / 2;
  logs.num_furniture_points = furnCompact.q.length / 2;
  logs.num_support_points = qSupport.length / 2;
  logs.num_seam_points = qSeam.length / 2;
  logs.camera_floor_xy = [rCamArr[0], rCamArr[1]];
  logs.manhattan_angle_deg = theta0 * 180 / Math.PI;
  logs.manhattan_concentration = concentration;
  logs.raw_wall_lines = rawLines.map((line, i) => ({
    id: i,
    direction_deg: directionRad(line) * 180 / Math.PI,
    inliers: line.inlierIdx.length,
    span_m: line.spanM,
    height_span_m: line.heightSpanM,
    median_residual_m: line.medianDistM,
    normal_agreement: line.meanNormalAgreement,
    score: line.score,
  }));
  if (rawLines.length < 2) return visibleOnly('fewer_than_two_reliable_wall_lines', visibleSqft, visibleM2, logs);
  if (concentration < params.MIN_MANHATTAN_CONC) return visibleOnly('weak_manhattan_frame', visibleSqft, visibleM2, logs);

  const candidates = [];
  for (let i = 0; i < rawLines.length; i++) {
    const cand = boundaryFromWallLine(i, rawLines[i], rWall, rSupport, rSeam, theta0, minWallInliers, params);
    if (cand) candidates.push(cand);
  }
  candidates.push(...seamOnlyCandidates(rSeam, rSupport, params));
  let selected = selectSides(candidates, rSupport);
  const inferred = inferMissingSides(selected, rSupport, params);
  selected = inferred.selected;
  const inferenceInfo = inferred.info;
  logs.boundary_candidates = candidates.map(c => ({ ...boundaryDict(c), side: c.side }));
  logs.selected_boundaries = Object.fromEntries(Object.entries(selected).map(([side, c]) => [side, boundaryDict(c)]));
  const missingAfterInfer = ['x_min', 'x_max', 'y_min', 'y_max'].filter(side => !selected[side]);
  const realBoundaries = Object.values(selected).filter(c => !c.inferred);
  const wallPlaneBoundaries = realBoundaries.filter(c => c.source.startsWith('wall_plane'));
  logs.num_real_boundaries = realBoundaries.length;
  logs.num_inferred_boundaries = inferenceInfo.num_inferred || 0;
  logs.n_boundaries = realBoundaries.length;
  Object.assign(logs, inferenceInfo);
  if (missingAfterInfer.length) {
    if (missingAfterInfer.length > 1) return visibleOnly('more_than_one_missing_boundary', visibleSqft, visibleM2, logs);
    return visibleOnly('not_enough_boundaries', visibleSqft, visibleM2, logs);
  }
  const { width, depth, area } = roomDimensions(selected);
  const [dimsOk, dimsReason] = dimensionGate(width, depth, area, params);
  const contain = supportContainment(selected, rSupport, params.SUPPORT_CONTAIN_BUFFER_M);
  const maxViolation = Math.max(...Object.values(selected).map(c => c.supportViolationFrac));
  Object.assign(logs, {
    room_width_m: width,
    room_depth_m: depth,
    room_area_m2: area,
    room_area_sqft: area * SQFT_PER_M2,
    room_dims_m: [width, depth],
    support_containment_frac: contain,
    max_support_violation_frac: maxViolation,
    completed_to_visible_ratio: visibleM2 > 0 ? area / visibleM2 : null,
  });
  if (!dimsOk) return visibleOnly(String(dimsReason), visibleSqft, visibleM2, logs);
  if (maxViolation > params.MAX_SUPPORT_VIOLATION_FRAC) return visibleOnly('support_violation_too_high', visibleSqft, visibleM2, logs);
  if (contain < params.MIN_SUPPORT_CONTAIN_LOW) return visibleOnly('support_containment_too_low', visibleSqft, visibleM2, logs);
  if (area < visibleM2 * 0.95) return visibleOnly('completed_area_smaller_than_visible_floor', visibleSqft, visibleM2, logs);
  if (realBoundaries.length < 3) return visibleOnly('fewer_than_three_real_boundaries', visibleSqft, visibleM2, logs);

  const avgBoundaryConf = Object.values(selected).reduce((s, c) => s + c.confidence, 0) / Object.values(selected).length;
  let confidence = clip01(
    0.25 * clip01((concentration - 0.40) / 0.30)
    + 0.25 * clip01((contain - 0.92) / 0.06)
    + 0.20 * clip01(realBoundaries.length / 4)
    + 0.15 * clip01(wallPlaneBoundaries.length / 2)
    + 0.15 * avgBoundaryConf
    - 0.20 * (inferenceInfo.num_inferred || 0)
  );
  if ((inferenceInfo.num_inferred || 0) > 0) confidence = Math.min(confidence, 0.58);
  let status = 'visible_only';
  if (
    confidence >= params.HIGH_CONF_THRESH
    && (inferenceInfo.num_inferred || 0) === 0
    && realBoundaries.length === 4
    && wallPlaneBoundaries.length >= 2
    && contain >= params.MIN_SUPPORT_CONTAIN_HIGH
    && concentration >= params.GOOD_MANHATTAN_CONC
  ) {
    status = 'completed';
  } else if (
    confidence >= params.LOW_CONF_THRESH
    && (inferenceInfo.num_inferred || 0) <= 1
    && contain >= params.MIN_SUPPORT_CONTAIN_LOW
  ) {
    status = 'low_confidence_completed';
  }
  if (status === 'visible_only') return visibleOnly('confidence_too_low', visibleSqft, visibleM2, logs);
  return {
    status,
    areaSqft: area * SQFT_PER_M2,
    visibleSqft,
    confidence,
    boundaries: Object.fromEntries(Object.entries(selected).map(([side, c]) => [side, boundaryDict(c)])),
    roomDimsM: [width, depth],
    areaM2: area,
    logs,
  };
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
