// SegFormer (ADE20K) floor segmentation via raw onnxruntime-web — no Transformers.js.
// Input pixel_values [1,3,512,512] fp32, ImageNet-normalized.
// Output logits [1,150,h,w]; argmax -> class; floor = class 3 (floor) or 28 (rug).
const MEAN = [0.485, 0.456, 0.406];
const STD = [0.229, 0.224, 0.225];
const SEG_SIZE = 512;
const FLOOR_CLASSES = new Set([3, 28]);

// Returns Int16Array(H*W): ADE20K class id at the requested output H×W.
export async function segmentClasses(ort, session, imgEl, H, W) {
  const c = document.createElement('canvas'); c.width = SEG_SIZE; c.height = SEG_SIZE;
  const ctx = c.getContext('2d'); ctx.drawImage(imgEl, 0, 0, SEG_SIZE, SEG_SIZE);
  const id = ctx.getImageData(0, 0, SEG_SIZE, SEG_SIZE).data;
  const plane = SEG_SIZE * SEG_SIZE;
  const chw = new Float32Array(3 * plane);
  for (let p = 0; p < plane; p++) {
    chw[p] = (id[p * 4] / 255 - MEAN[0]) / STD[0];
    chw[plane + p] = (id[p * 4 + 1] / 255 - MEAN[1]) / STD[1];
    chw[2 * plane + p] = (id[p * 4 + 2] / 255 - MEAN[2]) / STD[2];
  }
  const feeds = { pixel_values: new ort.Tensor('float32', chw, [1, 3, SEG_SIZE, SEG_SIZE]) };
  const out = await session.run(feeds);
  const logits = out.logits;
  const [, C, lh, lw] = logits.dims;
  const data = logits.data;
  // argmax over C at each (y,x) of the logits grid -> ADE20K class grid
  const segGrid = new Int16Array(lh * lw);
  const cstride = lh * lw;
  for (let y = 0; y < lh; y++) {
    for (let x = 0; x < lw; x++) {
      const base = y * lw + x;
      let bestC = 0, bestV = -Infinity;
      for (let cc = 0; cc < C; cc++) {
        const v = data[cc * cstride + base];
        if (v > bestV) { bestV = v; bestC = cc; }
      }
      segGrid[base] = bestC;
    }
  }
  // nearest-neighbour upsample logits grid -> H×W
  const seg = new Int16Array(H * W);
  for (let y = 0; y < H; y++) {
    const sy = Math.min(lh - 1, (y * lh / H) | 0);
    for (let x = 0; x < W; x++) {
      const sx = Math.min(lw - 1, (x * lw / W) | 0);
      seg[y * W + x] = segGrid[sy * lw + sx];
    }
  }
  return seg;
}

// Returns Uint8Array(H*W): 1 where floor, at the requested output H×W.
export async function segmentFloor(ort, session, imgEl, H, W) {
  const seg = await segmentClasses(ort, session, imgEl, H, W);
  const floor = new Uint8Array(H * W);
  for (let p = 0; p < H * W; p++) floor[p] = FLOOR_CLASSES.has(seg[p]) ? 1 : 0;
  return floor;
}
