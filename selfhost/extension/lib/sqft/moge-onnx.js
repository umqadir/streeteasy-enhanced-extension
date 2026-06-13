// MoGe-2 (ONNX) metric point map + normals + mask via onnxruntime-web.
// ImageNet normalization is baked into the ONNX graph, so the input is just
// RGB/255 in [1,3,H,W]. Outputs are channels-last (HWC): points[1,H,W,3],
// normal[1,H,W,3], mask[1,H,W] (sigmoid), scale[1] (metric meters).

const PATCH = 14; // DINOv2 patch size — H,W must be multiples of 14

function roundToPatch(x) { return Math.max(PATCH, Math.round(x / PATCH) * PATCH); }

// Draw the image into an [1,3,H,W] fp32 RGB/255 tensor, longest side ~maxSide.
export function preprocessImage(imgEl, maxSide = 644) {
  const iw = imgEl.naturalWidth || imgEl.width;
  const ih = imgEl.naturalHeight || imgEl.height;
  const ar = iw / ih;
  let W, H;
  if (iw >= ih) { W = maxSide; H = Math.round(maxSide / ar); }
  else { H = maxSide; W = Math.round(maxSide * ar); }
  W = roundToPatch(W); H = roundToPatch(H);

  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(imgEl, 0, 0, W, H);
  const id = ctx.getImageData(0, 0, W, H).data;
  const plane = H * W;
  const chw = new Float32Array(3 * plane);
  for (let p = 0; p < plane; p++) {
    chw[p] = id[p * 4] / 255;
    chw[plane + p] = id[p * 4 + 1] / 255;
    chw[2 * plane + p] = id[p * 4 + 2] / 255;
  }
  return { chw, H, W };
}

// Run MoGe. Returns metric point map (Float32Array HWC), normals (HWC), and a
// Uint8 validity mask, at H×W.
export async function runMoge(ort, session, imgEl, { tokens = 1800, maxSide = 644 } = {}) {
  const { chw, H, W } = preprocessImage(imgEl, maxSide);
  const feeds = {};
  for (const name of session.inputNames) {
    if (name.toLowerCase().includes('token')) {
      feeds[name] = new ort.Tensor('int64', new BigInt64Array([BigInt(tokens)]), []);
    } else {
      feeds[name] = new ort.Tensor('float32', chw, [1, 3, H, W]);
    }
  }
  const out = await session.run(feeds);
  const scale = out['scale'].data[0];
  const rawPts = out['points'].data;
  const normal = out['normal'].data;
  const rawMask = out['mask'].data;

  const points = new Float32Array(rawPts.length);
  for (let i = 0; i < rawPts.length; i++) points[i] = rawPts[i] * scale;
  const mask = new Uint8Array(H * W);
  for (let i = 0; i < H * W; i++) mask[i] = rawMask[i] > 0.5 ? 1 : 0;

  return { points, normal, mask, H, W, scale };
}
