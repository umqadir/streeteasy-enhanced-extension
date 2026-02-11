#!/usr/bin/env python3
"""
Visual photo picker for sqft-from-photos experiments.

Serves a local web UI for browsing listings, selecting photos, and running v2b.

Usage:
    uv run pick.py              # open browser at localhost:8787
    uv run pick.py 1            # open directly to listing_001
"""
from __future__ import annotations

import json
import mimetypes
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── Config ────────────────────────────────────────────────────────────────
EVAL_DIR = (
    Path(__file__).resolve().parent.parent
    / "sample-collection" / "streeteasy_eval_dataset" / "photos"
)
LISTINGS_JSON = (
    Path(__file__).resolve().parent.parent
    / "sample-collection" / "streeteasy_eval_dataset" / "listings.json"
)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PORT = 8787


# ── Data helpers ──────────────────────────────────────────────────────────
_meta_cache: dict | None = None


def get_meta() -> dict[str, dict]:
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache
    _meta_cache = {}
    if LISTINGS_JSON.exists():
        data = json.loads(LISTINGS_JSON.read_text())
        for entry in data.get("listings", []):
            _meta_cache[entry["id"]] = entry
    return _meta_cache


def find_images(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")
    )


def list_listings() -> list[dict]:
    if not EVAL_DIR.is_dir():
        return []
    meta = get_meta()
    out = []
    for d in sorted(EVAL_DIR.iterdir()):
        if not d.is_dir():
            continue
        lid = d.name
        imgs = find_images(d)
        info = meta.get(lid, {})
        first = f"/photo/{lid}/{imgs[0].name}" if imgs else ""
        out.append({
            "id": lid,
            "num": int(lid.split("_")[1]),
            "photo_count": len(imgs),
            "sqft": info.get("sqft"),
            "title": info.get("title", ""),
            "thumb": first,
        })
    return out


def list_photos(listing_id: str) -> list[dict]:
    folder = EVAL_DIR / listing_id
    if not folder.is_dir():
        return []
    return [
        {"name": p.name, "url": f"/photo/{listing_id}/{p.name}"}
        for p in find_images(folder)
    ]


# ── Pipeline ──────────────────────────────────────────────────────────────
_pipeline_lock = threading.Lock()


def run_pipeline(listing_id: str, photo_names: list[str]) -> dict:
    folder = EVAL_DIR / listing_id
    images = [folder / name for name in photo_names]
    for img in images:
        if not img.exists():
            return {"error": f"not found: {img}"}

    from estimate_v2b import run_pipeline as _run

    with _pipeline_lock:
        result = _run(images, interactive=False)

    meta = get_meta().get(listing_id, {})
    return {
        "variant": "v2b",
        "listing_id": listing_id,
        "ground_truth_sqft": meta.get("sqft"),
        "sqft": round(result.sqft, 1),
        "area_m2": round(result.area_m2, 2),
        "ci_lo": round(result.ci_lo, 1),
        "ci_hi": round(result.ci_hi, 1),
        "n_images": result.n_images,
        "elapsed_s": round(result.elapsed_s, 1),
        "per_image": [
            {
                "image": Path(pr.image_path).name,
                "area_sqft": round(pr.area_sqft, 1),
                "area_m2": round(pr.area_m2, 2),
                "floor_mask_frac": round(pr.floor_mask_frac, 3),
                "n_floor_points_3d": pr.n_floor_points_3d,
                "plane_residual_m": round(pr.plane_residual, 4),
            }
            for pr in result.per_image
        ],
    }


# ── HTML ──────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sqft-from-photos picker</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #111113;
    --surface: #1a1a1f;
    --surface2: #232328;
    --border: #2e2e35;
    --text: #e4e4e8;
    --text2: #8e8e96;
    --accent: #5b8af5;
    --accent2: #3d6de0;
    --green: #3ddc84;
    --orange: #f0a030;
    --red: #f05050;
    --radius: 8px;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    min-height: 100vh;
  }

  /* ── Header ─────────────────────────────── */
  header {
    padding: 20px 28px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
  }
  header h1 { font-size: 18px; font-weight: 600; }
  header .meta { color: var(--text2); font-size: 13px; }

  nav {
    display: flex;
    gap: 6px;
    margin-left: auto;
  }
  nav button, .btn {
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 14px;
    border-radius: var(--radius);
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
  }
  nav button:hover, .btn:hover { background: var(--border); }
  .btn-primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }
  .btn-primary:hover { background: var(--accent2); }
  .btn-primary:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* ── Breadcrumb ─────────────────────────── */
  .breadcrumb {
    padding: 12px 28px;
    font-size: 13px;
    color: var(--text2);
    border-bottom: 1px solid var(--border);
  }
  .breadcrumb a {
    color: var(--accent);
    text-decoration: none;
    cursor: pointer;
  }
  .breadcrumb a:hover { text-decoration: underline; }
  .breadcrumb span { color: var(--text2); }

  /* ── Listings grid ──────────────────────── */
  #listings-view { padding: 20px 28px; }

  .filter-bar {
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
    align-items: center;
  }
  .filter-bar input {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: var(--radius);
    font-size: 13px;
    width: 200px;
  }
  .filter-bar label {
    font-size: 13px;
    color: var(--text2);
    display: flex;
    align-items: center;
    gap: 5px;
    cursor: pointer;
  }

  .listings-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
  }

  .listing-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    cursor: pointer;
    transition: border-color 0.15s, transform 0.1s;
  }
  .listing-card:hover {
    border-color: var(--accent);
    transform: translateY(-1px);
  }
  .listing-card img {
    width: 100%;
    aspect-ratio: 4/3;
    object-fit: cover;
    display: block;
    background: var(--surface2);
  }
  .listing-card .info {
    padding: 8px 10px;
  }
  .listing-card .num {
    font-weight: 600;
    font-size: 14px;
  }
  .listing-card .detail {
    font-size: 12px;
    color: var(--text2);
  }
  .listing-card .sqft-badge {
    display: inline-block;
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 4px;
    margin-top: 3px;
  }
  .sqft-badge.has { background: rgba(61,220,132,.15); color: var(--green); }
  .sqft-badge.none { background: rgba(142,142,150,.1); color: var(--text2); }

  /* ── Photos grid ────────────────────────── */
  #photos-view { padding: 20px 28px; display: none; }

  .photos-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .photos-header h2 { font-size: 16px; font-weight: 600; }
  .photos-header .ground-truth {
    font-size: 13px;
    color: var(--green);
    background: rgba(61,220,132,.1);
    padding: 3px 10px;
    border-radius: var(--radius);
  }
  .selection-info {
    font-size: 13px;
    color: var(--text2);
    margin-left: auto;
  }

  .photos-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px;
    margin-bottom: 20px;
  }

  .photo-card {
    position: relative;
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    cursor: pointer;
    transition: border-color 0.15s;
  }
  .photo-card:hover { border-color: #555; }
  .photo-card.selected { border-color: var(--accent); }
  .photo-card img {
    width: 100%;
    aspect-ratio: 4/3;
    object-fit: cover;
    display: block;
  }
  .photo-card .label {
    position: absolute;
    top: 6px;
    left: 6px;
    background: rgba(0,0,0,.7);
    color: #fff;
    font-size: 12px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 4px;
    font-variant-numeric: tabular-nums;
  }
  .photo-card .check {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 2px solid rgba(255,255,255,.4);
    background: rgba(0,0,0,.4);
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .photo-card.selected .check {
    background: var(--accent);
    border-color: var(--accent);
  }
  .photo-card.selected .check::after {
    content: '';
    width: 6px;
    height: 10px;
    border: solid #fff;
    border-width: 0 2px 2px 0;
    transform: rotate(45deg) translate(-1px, -1px);
  }
  .photo-card .filename {
    padding: 5px 8px;
    font-size: 11px;
    color: var(--text2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* ── Run bar ────────────────────────────── */
  .run-bar {
    position: sticky;
    bottom: 0;
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 12px 28px;
    display: flex;
    align-items: center;
    gap: 14px;
  }

  /* ── Results ────────────────────────────── */
  #results { display: none; }

  .results-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    margin: 0 28px 20px;
  }
  .results-panel h3 {
    font-size: 15px;
    margin-bottom: 12px;
    font-weight: 600;
  }
  .result-stat {
    display: inline-block;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 18px;
    margin: 0 8px 8px 0;
    text-align: center;
    min-width: 120px;
  }
  .result-stat .value {
    font-size: 24px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .result-stat .label {
    font-size: 11px;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 2px;
  }
  .result-stat.primary .value { color: var(--accent); }

  .per-image-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin-top: 12px;
  }
  .per-image-table th {
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    color: var(--text2);
    font-weight: 500;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .per-image-table td {
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    font-variant-numeric: tabular-nums;
  }
  .per-image-table tr:last-child td { border-bottom: none; }

  .spinner {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .status-msg { font-size: 13px; color: var(--text2); }
</style>
</head>
<body>

<header>
  <h1>sqft-from-photos</h1>
  <span class="meta" id="header-meta"></span>
</header>

<div class="breadcrumb" id="breadcrumb">
  <a onclick="showListings()">listings</a>
</div>

<!-- Listings view -->
<div id="listings-view">
  <div class="filter-bar">
    <input type="text" id="search" placeholder="search listings..." oninput="filterListings()">
    <label><input type="checkbox" id="sqft-only" onchange="filterListings()"> has sqft</label>
  </div>
  <div class="listings-grid" id="listings-grid"></div>
</div>

<!-- Photos view -->
<div id="photos-view">
  <div class="photos-header">
    <h2 id="listing-title"></h2>
    <span class="ground-truth" id="ground-truth" style="display:none"></span>
    <span class="selection-info" id="selection-info">click photos to select</span>
  </div>
  <div class="photos-grid" id="photos-grid"></div>
</div>

<!-- Results -->
<div id="results">
  <div class="results-panel" id="results-panel"></div>
</div>

<!-- Run bar -->
<div class="run-bar" id="run-bar" style="display:none">
  <button class="btn-primary" id="run-btn" onclick="runPipeline()" disabled>
    run v2b
  </button>
  <button class="btn" onclick="clearSelection()">clear</button>
  <button class="btn" onclick="selectAll()">all</button>
  <span class="status-msg" id="status-msg"></span>
</div>

<script>
let listings = [];
let currentListing = null;
let currentPhotos = [];
let selected = new Set();

// ── Init ──────────────────────────────────
async function init() {
  const res = await fetch('/api/listings');
  listings = await res.json();
  const withSqft = listings.filter(l => l.sqft).length;
  document.getElementById('header-meta').textContent =
    `${listings.length} listings \u00b7 ${withSqft} with sqft`;
  renderListings(listings);

  // Check for hash-based deep link
  if (location.hash.startsWith('#listing_')) {
    const id = location.hash.slice(1);
    openListing(id);
  }
}

// ── Listings ──────────────────────────────
function renderListings(items) {
  const grid = document.getElementById('listings-grid');
  grid.innerHTML = items.map(l => `
    <div class="listing-card" onclick="openListing('${l.id}')">
      <img src="${l.thumb}" loading="lazy" alt="">
      <div class="info">
        <div class="num">#${l.num}</div>
        <div class="detail">${l.photo_count} photos</div>
        ${l.sqft
          ? `<span class="sqft-badge has">${Math.round(l.sqft)} sqft</span>`
          : `<span class="sqft-badge none">no sqft</span>`}
      </div>
    </div>
  `).join('');
}

function filterListings() {
  const q = document.getElementById('search').value.toLowerCase();
  const sqftOnly = document.getElementById('sqft-only').checked;
  const filtered = listings.filter(l => {
    if (sqftOnly && !l.sqft) return false;
    if (q && !`${l.num} ${l.title}`.toLowerCase().includes(q)) return false;
    return true;
  });
  renderListings(filtered);
}

function showListings() {
  document.getElementById('listings-view').style.display = '';
  document.getElementById('photos-view').style.display = 'none';
  document.getElementById('results').style.display = 'none';
  document.getElementById('run-bar').style.display = 'none';
  document.getElementById('breadcrumb').innerHTML =
    '<a onclick="showListings()">listings</a>';
  location.hash = '';
  currentListing = null;
  selected.clear();
}

// ── Photos ────────────────────────────────
async function openListing(id) {
  const res = await fetch(`/api/photos?listing=${id}`);
  currentPhotos = await res.json();
  currentListing = listings.find(l => l.id === id) || { id, num: id };
  selected.clear();

  document.getElementById('listings-view').style.display = 'none';
  document.getElementById('photos-view').style.display = '';
  document.getElementById('run-bar').style.display = 'flex';
  document.getElementById('results').style.display = 'none';
  document.getElementById('results-panel').innerHTML = '';
  document.getElementById('status-msg').textContent = '';

  const title = `#${currentListing.num}`;
  document.getElementById('listing-title').textContent = title;

  const gt = document.getElementById('ground-truth');
  if (currentListing.sqft) {
    gt.textContent = `ground truth: ${Math.round(currentListing.sqft)} sqft`;
    gt.style.display = '';
  } else {
    gt.style.display = 'none';
  }

  document.getElementById('breadcrumb').innerHTML =
    `<a onclick="showListings()">listings</a> <span>/</span> <a>${currentListing.id}</a>`;

  location.hash = id;
  renderPhotos();
  updateSelectionUI();
}

function renderPhotos() {
  const grid = document.getElementById('photos-grid');
  grid.innerHTML = currentPhotos.map((p, i) => `
    <div class="photo-card ${selected.has(i) ? 'selected' : ''}"
         onclick="togglePhoto(${i})" id="photo-${i}">
      <img src="${p.url}" loading="lazy" alt="">
      <span class="label">${i}</span>
      <span class="check"></span>
      <div class="filename">${p.name}</div>
    </div>
  `).join('');
}

function togglePhoto(i) {
  if (selected.has(i)) selected.delete(i);
  else selected.add(i);
  const card = document.getElementById(`photo-${i}`);
  card.classList.toggle('selected', selected.has(i));
  updateSelectionUI();
}

function clearSelection() {
  selected.clear();
  renderPhotos();
  updateSelectionUI();
}

function selectAll() {
  currentPhotos.forEach((_, i) => selected.add(i));
  renderPhotos();
  updateSelectionUI();
}

function updateSelectionUI() {
  const n = selected.size;
  document.getElementById('selection-info').textContent =
    n === 0 ? 'click photos to select' : `${n} photo${n > 1 ? 's' : ''} selected`;
  document.getElementById('run-btn').disabled = n === 0;
  document.getElementById('run-btn').textContent =
    n === 0 ? 'run v2b' : `run v2b on ${n} photo${n > 1 ? 's' : ''}`;
}

// ── Run pipeline ──────────────────────────
async function runPipeline() {
  const btn = document.getElementById('run-btn');
  const status = document.getElementById('status-msg');
  const photos = [...selected].sort((a,b) => a-b).map(i => currentPhotos[i].name);

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  status.textContent = 'loading models + running inference...';

  const t0 = Date.now();
  const timer = setInterval(() => {
    const s = ((Date.now() - t0) / 1000).toFixed(0);
    status.textContent = `running... ${s}s`;
  }, 1000);

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listing: currentListing.id, photos }),
    });
    const data = await res.json();
    clearInterval(timer);

    if (data.error) {
      status.textContent = `error: ${data.error}`;
      btn.textContent = 'run v2b';
      btn.disabled = false;
      return;
    }

    status.textContent = `done in ${data.elapsed_s.toFixed(1)}s`;
    showResults(data);
  } catch (e) {
    clearInterval(timer);
    status.textContent = `error: ${e.message}`;
  }
  btn.innerHTML = `run v2b on ${selected.size} photo${selected.size > 1 ? 's' : ''}`;
  btn.disabled = false;
}

function showResults(data) {
  const panel = document.getElementById('results-panel');
  const gt = data.ground_truth_sqft;
  const errorPct = gt ? ((data.sqft - gt) / gt * 100).toFixed(0) : null;
  const errorColor = errorPct !== null
    ? (Math.abs(errorPct) < 20 ? 'var(--green)' : Math.abs(errorPct) < 50 ? 'var(--orange)' : 'var(--red)')
    : '';

  panel.innerHTML = `
    <h3>results</h3>
    <div>
      <div class="result-stat primary">
        <div class="value">${Math.round(data.sqft)}</div>
        <div class="label">sqft estimate</div>
      </div>
      <div class="result-stat">
        <div class="value">${Math.round(data.ci_lo)}&ndash;${Math.round(data.ci_hi)}</div>
        <div class="label">90% CI</div>
      </div>
      ${gt ? `
      <div class="result-stat">
        <div class="value">${Math.round(gt)}</div>
        <div class="label">ground truth</div>
      </div>
      <div class="result-stat">
        <div class="value" style="color:${errorColor}">${errorPct > 0 ? '+' : ''}${errorPct}%</div>
        <div class="label">error</div>
      </div>` : ''}
      <div class="result-stat">
        <div class="value">${data.elapsed_s.toFixed(1)}s</div>
        <div class="label">time</div>
      </div>
    </div>
    <table class="per-image-table">
      <thead>
        <tr><th>image</th><th>sqft</th><th>floor %</th><th>points</th><th>residual</th></tr>
      </thead>
      <tbody>
        ${data.per_image.map(p => `
          <tr>
            <td>${p.image}</td>
            <td>${Math.round(p.area_sqft)}</td>
            <td>${(p.floor_mask_frac * 100).toFixed(0)}%</td>
            <td>${p.n_floor_points_3d.toLocaleString()}</td>
            <td>${p.plane_residual_m.toFixed(3)}m</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  document.getElementById('results').style.display = '';
}

init();
</script>
</body>
</html>"""


# ── Server ────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quieter logging — suppress favicon and API noise
        try:
            msg = str(args[0]) if args else ''
            if '/api/' in msg or 'favicon' in msg:
                return
        except Exception:
            pass
        super().log_message(format, *args)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path):
        if not path.exists():
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/listings":
            self._json(list_listings())

        elif path == "/api/photos":
            listing = qs.get("listing", [""])[0]
            self._json(list_photos(listing))

        elif path.startswith("/photo/"):
            # /photo/listing_001/photo_00.jpg
            parts = path.split("/")
            if len(parts) >= 4:
                photo_path = EVAL_DIR / parts[2] / parts[3]
                self._serve_file(photo_path)
            else:
                self.send_error(404)

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/run":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            listing = body.get("listing", "")
            photos = body.get("photos", [])
            if not listing or not photos:
                self._json({"error": "listing and photos required"}, 400)
                return
            print(f"  running v2b: {listing}, {len(photos)} photos...")
            result = run_pipeline(listing, photos)
            print(f"  done: {result.get('sqft', '?')} sqft")
            self._json(result)
        else:
            self.send_error(404)


def main():
    start_listing = None
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.isdigit():
            start_listing = f"listing_{int(arg):03d}"
        elif arg.startswith("listing_"):
            start_listing = arg

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    if start_listing:
        url += f"#{start_listing}"

    print(f"\n  sqft-from-photos picker")
    print(f"  {url}")
    print(f"  ctrl-c to stop\n")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
        server.server_close()


if __name__ == "__main__":
    main()
