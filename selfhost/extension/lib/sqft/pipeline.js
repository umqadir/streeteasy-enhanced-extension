// In-browser single-image room-area pipeline.
// MoGe-2 (metric geometry) + SegFormer (floor mask), both via onnxruntime-web
// WebGPU, then RANSAC plane + alpha-shape area. Runs in an extension page
// (offscreen document) with WebGPU; model weights are fetched once and cached.

import * as ort from '../ort/ort.webgpu.min.mjs';
import { computeFloorArea } from './geometry.js';
import { segmentFloor } from './seg-onnx.js';
import { runMoge } from './moge-onnx.js';

// ORT loads its wasm from the packaged lib/ort/ directory (no remote code).
ort.env.wasm.wasmPaths = chrome.runtime.getURL('lib/ort/');
ort.env.wasm.numThreads = 1;

const MODELS = {
  moge: 'https://huggingface.co/Ruicheng/moge-2-vits-normal-onnx/resolve/main/model.onnx',
  seg: 'https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/resolve/main/onnx/model.onnx',
};

let _mogeSession = null;
let _segSession = null;
let _backend = null; // 'webgpu' | 'wasm'
let _forceWasm = false;

/** Force the WASM execution provider (used in contexts where WebGPU is unreliable). */
export function setForceWasm(v) { _forceWasm = !!v; }

async function createSession(url) {
  // Cache the fetched weights in CacheStorage so first-run download is one-time.
  let buf;
  try {
    const cache = await caches.open('sleepeasy-models-v1');
    let res = await cache.match(url);
    if (!res) { await cache.add(url); res = await cache.match(url); }
    buf = await res.arrayBuffer();
  } catch {
    buf = await (await fetch(url)).arrayBuffer();
  }
  const providers = (!_forceWasm && navigator.gpu) ? ['webgpu', 'wasm'] : ['wasm'];
  const session = await ort.InferenceSession.create(buf, {
    executionProviders: providers,
    graphOptimizationLevel: 'all',
  });
  return session;
}

export async function ensureReady(onProgress = () => {}) {
  if (_mogeSession && _segSession) return { backend: _backend };
  onProgress('loading-segmenter');
  _segSession = _segSession || await createSession(MODELS.seg);
  onProgress('loading-depth');
  _mogeSession = _mogeSession || await createSession(MODELS.moge);
  _backend = (!_forceWasm && navigator.gpu) ? 'webgpu' : 'wasm';
  return { backend: _backend };
}

/**
 * Estimate visible-floor area for one image.
 * @param {HTMLImageElement} imgEl
 * @returns {Promise<{sqft:number, convexSqft:number, areaM2:number, backend:string,
 *                     nFloor:number, nInliers:number, ok:boolean, timings:object}>}
 */
export async function estimate(imgEl, { tokens = 1800, maxSide = 644 } = {}) {
  await ensureReady();
  const timings = {};
  let t = performance.now();

  const floorAtFull = async (H, W) => segmentFloor(ort, _segSession, imgEl, H, W);

  const moge = await runMoge(ort, _mogeSession, imgEl, { tokens, maxSide });
  timings.mogeMs = Math.round(performance.now() - t); t = performance.now();

  const floor = await floorAtFull(moge.H, moge.W);
  timings.segMs = Math.round(performance.now() - t); t = performance.now();

  const area = computeFloorArea({
    points: moge.points, normal: moge.normal, mask: moge.mask, floor,
    H: moge.H, W: moge.W,
  });
  timings.areaMs = Math.round(performance.now() - t);

  return {
    sqft: Math.round(area.sqft || 0),
    convexSqft: Math.round(area.convexSqft || 0),
    areaM2: area.areaM2 || 0,
    backend: _backend,
    nFloor: area.nFloor || 0,
    nInliers: area.nInliers || 0,
    ok: !!area.ok,
    timings,
  };
}
