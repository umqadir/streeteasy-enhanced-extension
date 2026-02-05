#!/usr/bin/env python3

"""
Local-only HTML curation UI for creating a "clean set" of listings.

Goal:
  Click-to-exclude bad photos (exteriors/amenities/etc), optionally type sqft for that listing, then export
  a tiny dataset folder you can upload to RunPod and run immediately.

Export format:
  <out_dir>/
    listings.json
    photos/<listing_id>/photo_00.jpg ...

RunPod then:
  uv run cv-pipeline eval-streeteasy --dataset /workspace/data/streeteasy_clean_set/listings.json --has-sqft
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import sys
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps


def _json(obj: object) -> bytes:
    return (json.dumps(obj, indent=2) + "\n").encode("utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_relpath(p: str) -> str:
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if ".." in Path(p).parts:
        raise ValueError("path traversal not allowed")
    return p


def _load_dataset(dataset_path: Path) -> tuple[Path, list[dict[str, object]]]:
    obj = _read_json(dataset_path)
    if not isinstance(obj, dict) or "listings" not in obj:
        raise SystemExit("Expected a dataset JSON with top-level key: listings")
    listings = obj.get("listings", [])
    if not isinstance(listings, list):
        raise SystemExit("dataset.listings must be a list")
    return dataset_path.parent, [x for x in listings if isinstance(x, dict)]


def _list_listing_photos(dataset_root: Path, listing: dict[str, object]) -> list[str]:
    photo_paths = listing.get("photo_paths")
    if isinstance(photo_paths, list) and photo_paths and all(isinstance(p, str) for p in photo_paths):
        return [_safe_relpath(p) for p in photo_paths]

    listing_id = str(listing.get("id") or "").strip()
    if not listing_id:
        return []
    photos_dir = dataset_root / "photos" / listing_id
    if not photos_dir.exists():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    paths = sorted([p for p in photos_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts])
    return [_safe_relpath(str(p.relative_to(dataset_root).as_posix())) for p in paths]


def _thumb_cache_dir() -> Path:
    p = Path.home() / ".cache" / "cv_pipeline" / "curate_web_thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _thumb_path_for(relpath: str, width: int) -> Path:
    safe = relpath.replace("/", "__")
    return _thumb_cache_dir() / f"{safe}__w{width}.jpg"


def _make_thumb(src: Path, dst: Path, width: int) -> None:
    try:
        img = Image.open(src)
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        img = Image.new("RGB", (width, width), (200, 200, 200))
    w, h = img.size
    if w <= 0 or h <= 0:
        img = Image.new("RGB", (width, width), (200, 200, 200))
    else:
        scale = float(width) / float(w)
        new_h = max(1, int(round(h * scale)))
        img = img.resize((width, new_h), Image.Resampling.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="JPEG", quality=85, optimize=True)


@dataclass(frozen=True)
class AppConfig:
    dataset_path: Path
    dataset_root: Path
    out_dir: Path
    port: int
    host: str


INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Curate clean set</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 0; }
    header { padding: 12px 16px; border-bottom: 1px solid #ddd; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    header code { background:#f3f3f3; padding:2px 6px; border-radius:8px; }
    .wrap { display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 58px); }
    aside { border-right: 1px solid #ddd; overflow: auto; padding: 10px; }
    main { overflow: auto; padding: 12px; }
    .list { display: flex; flex-direction: column; gap: 6px; }
    .row { padding: 8px; border: 1px solid #ddd; border-radius: 10px; cursor: pointer; }
    .row.active { border-color: #000; }
    .row small { color: #666; display:block; margin-top: 2px; }
    .controls { display:flex; gap:8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
    button { border: 1px solid #bbb; background: #fff; border-radius: 10px; padding: 8px 10px; cursor: pointer; }
    button.primary { background:#000; color:#fff; border-color:#000; }
    button.warn { background:#ff4d4d; color:#fff; border-color:#ff4d4d; }
    input { border:1px solid #bbb; border-radius: 10px; padding: 8px 10px; }
    .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
    .tile { border: 2px solid #ddd; border-radius: 12px; padding: 6px; }
    .tile.excluded { opacity: 0.35; border-color: #ff4d4d; }
    .tile img { width: 100%; height: auto; border-radius: 8px; display:block; }
    .tile .cap { font-size: 12px; color:#333; margin-top: 6px; display:flex; justify-content: space-between; gap: 8px; }
    .pill { font-size: 11px; padding: 2px 6px; border-radius: 999px; background:#f3f3f3; color:#333; }
    .status { color:#333; font-size: 13px; }
  </style>
</head>
<body>
  <header>
    <b>Curate clean set</b>
    <span class="status" id="status"></span>
    <span style="flex:1"></span>
    <span>Export dir: <code id="outDir"></code></span>
  </header>
  <div class="wrap">
    <aside>
      <div class="controls">
        <label><input type="checkbox" id="onlyHasSqft" /> has sqft</label>
        <input id="filterText" placeholder="filter listing id..." style="width: 100%;" />
      </div>
      <div class="list" id="listingList"></div>
    </aside>
    <main>
      <div class="controls">
        <button id="includeAll">Include all</button>
        <button id="excludeAll">Exclude all</button>
        <button id="invert">Invert</button>
        <span style="flex:1"></span>
        <label>Sqft <input id="sqft" placeholder="e.g. 850" style="width:110px" /></label>
        <button class="primary" id="exportBatch">Export batch (<span id="batchCount">0</span>)</button>
        <button class="warn" id="clearBatch">Clear batch</button>
      </div>
      <div class="controls">
        <span id="listingMeta" class="status"></span>
      </div>
      <div class="grid" id="grid"></div>
    </main>
  </div>
<script>
const state = {
  listings: [],
  activeId: null,
  photos: [],
  excluded: new Set(),
  urlById: new Map(),
  hasSqftById: new Map(),  // labelable: flagged or has numeric sqft
  flagById: new Map(),     // from source dataset's has_sqft_data field
  sqftById: new Map(),
};

const STORAGE_KEY = 'sqft_from_photos_curate_batch_v1';

function loadBatch(){
  try{
    const raw = localStorage.getItem(STORAGE_KEY);
    if(!raw) return {};
    const obj = JSON.parse(raw);
    return (obj && typeof obj === 'object') ? obj : {};
  }catch(_){ return {}; }
}

function saveBatch(batch){
  localStorage.setItem(STORAGE_KEY, JSON.stringify(batch));
  qs('batchCount').textContent = String(Object.keys(batch).length);
}

function getBatch(){
  return loadBatch();
}

function setBatchItem(listingId, excludedArr, sqft){
  const batch = getBatch();
  batch[listingId] = { excluded: excludedArr, sqft: sqft };
  saveBatch(batch);
}

function removeBatchItem(listingId){
  const batch = getBatch();
  delete batch[listingId];
  saveBatch(batch);
}

function clearBatch(){
  saveBatch({});
}

function syncBatchFromCurrentListing(){
  const id = state.activeId;
  if(!id) return;
  const total = state.photos.length;
  const excluded = state.excluded.size;
  const included = total - excluded;
  const sqft = (state.sqftById.get(id) || '').trim();
  if(included > 0){
    setBatchItem(id, Array.from(state.excluded), sqft ? Number(sqft) : null);
  }else{
    removeBatchItem(id);
  }
}
function qs(id){ return document.getElementById(id); }
function setStatus(msg){ qs('status').textContent = msg; }
async function apiGet(url){ const r=await fetch(url); if(!r.ok) throw new Error(`GET ${url} => ${r.status}`); return await r.json(); }
async function apiPost(url, body){
  const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if(!r.ok) throw new Error(`POST ${url} => ${r.status}: ${await r.text()}`);
  return await r.json();
}
function renderListings(){
  const list = qs('listingList'); list.innerHTML='';
  const onlyHasSqft = qs('onlyHasSqft').checked;
  const q = qs('filterText').value.trim().toLowerCase();
  const batch = getBatch();
  let shown=0;
  for(const l of state.listings){
    const id=l.id;
    if(q && !id.toLowerCase().includes(q)) continue;
    const queued = (batch[id] != null);
    const flagged = !!state.flagById.get(id);
    const sqft = state.sqftById.get(id)||'';
    const labelable = flagged || !!sqft || queued || !!state.hasSqftById.get(id);
    if(onlyHasSqft && !labelable) continue;
    shown++;
    const div=document.createElement('div');
    div.className='row'+(id===state.activeId?' active':'');
    const needsSqft = flagged && !sqft;
    div.innerHTML=`<b>${id}</b>
      ${sqft?`<span class="pill">${sqft} sqft</span>`:''}
      ${flagged?`<span class="pill">flagged</span>`:''}
      ${needsSqft?`<span class="pill">needs sqft</span>`:''}
      ${queued?`<span class="pill">queued</span>`:''}
      <small>${(l.photo_count||0)} photos</small>`;
    div.onclick=()=>loadListing(id);
    list.appendChild(div);
  }
  if(shown===0){ const d=document.createElement('div'); d.className='status'; d.textContent='No listings match.'; list.appendChild(d); }
}
function renderGrid(){
  const grid=qs('grid'); grid.innerHTML='';
  for(let i=0;i<state.photos.length;i++){
    const rel=state.photos[i];
    const tile=document.createElement('div');
    tile.className='tile'+(state.excluded.has(rel)?' excluded':'');
    const thumb=`/thumb?path=${encodeURIComponent(rel)}&w=320`;
    tile.innerHTML=`<img src="${thumb}" loading="lazy" /><div class="cap"><span>#${String(i).padStart(3,'0')}</span><span class="pill">${state.excluded.has(rel)?'excluded':'included'}</span></div>`;
    tile.onclick=()=>{
      if(state.excluded.has(rel)) state.excluded.delete(rel); else state.excluded.add(rel);
      syncBatchFromCurrentListing();
      renderGrid();
      updateMeta();
      renderListings();
    };
    grid.appendChild(tile);
  }
}
function updateMeta(){
  const id=state.activeId; if(!id) return;
  const total=state.photos.length; const excluded=state.excluded.size; const included=total-excluded;
  const url=state.urlById.get(id)||'';
  qs('listingMeta').innerHTML=`<b>${id}</b> — included ${included}/${total} — <a href="${url}" target="_blank" rel="noreferrer noopener">open listing</a>`;
  qs('sqft').value=state.sqftById.get(id)||'';
}
async function loadListing(id){
  state.activeId=id; setStatus('Loading photos…');
  const data=await apiGet(`/api/listing?id=${encodeURIComponent(id)}`);
  state.photos=data.photo_paths;
  // Default: everything excluded unless the listing is already in the batch.
  state.excluded=new Set(state.photos);
  if(data.sqft!=null) state.sqftById.set(id,String(data.sqft));
  // Restore from batch (if queued)
  const batch = getBatch();
  if(batch[id]){
    const b = batch[id];
    if(Array.isArray(b.excluded)) state.excluded = new Set(b.excluded);
    if(b.sqft != null) state.sqftById.set(id, String(b.sqft));
  }
  renderListings(); renderGrid(); updateMeta(); setStatus('');
}
qs('onlyHasSqft').addEventListener('change', renderListings);
qs('filterText').addEventListener('input', renderListings);
qs('includeAll').onclick=()=>{ state.excluded=new Set(); syncBatchFromCurrentListing(); renderGrid(); updateMeta(); renderListings(); };
qs('excludeAll').onclick=()=>{ state.excluded=new Set(state.photos); syncBatchFromCurrentListing(); renderGrid(); updateMeta(); renderListings(); };
qs('invert').onclick=()=>{ const next=new Set(); for(const rel of state.photos){ if(!state.excluded.has(rel)) next.add(rel); } state.excluded=next; syncBatchFromCurrentListing(); renderGrid(); updateMeta(); renderListings(); };
qs('sqft').addEventListener('input',()=>{ const id=state.activeId; if(!id) return; const v=qs('sqft').value.trim(); if(v) state.sqftById.set(id,v); else state.sqftById.delete(id); syncBatchFromCurrentListing(); renderListings(); });

qs('exportBatch').onclick=async()=>{
  const batch=getBatch();
  const ids=Object.keys(batch);
  if(ids.length===0){ setStatus('Batch is empty'); return; }
  setStatus(`Exporting ${ids.length} listings…`);
  const items = ids.map(id => ({ listing_id:id, excluded: batch[id].excluded || [], sqft: batch[id].sqft ?? null }));
  const res = await apiPost('/api/export_many', { items });
  setStatus(`Exported ${res.ok_count}/${res.n} listings → ${res.out_dir}`);
  renderListings();
};

qs('clearBatch').onclick=()=>{
  clearBatch();
  setStatus('Cleared batch');
  renderListings();
};
async function init(){
  const meta=await apiGet('/api/meta'); qs('outDir').textContent=meta.out_dir;
  qs('batchCount').textContent = String(Object.keys(getBatch()).length);
  const data=await apiGet('/api/listings'); state.listings=data.listings;
  for(const l of state.listings){
    state.urlById.set(l.id,l.url||'');
    const flagged = !!l.has_sqft_data;
    state.flagById.set(l.id, flagged);
    if(typeof l.sqft === 'number' && Number.isFinite(l.sqft)){
      state.sqftById.set(l.id, String(Math.round(l.sqft)));
      state.hasSqftById.set(l.id, true);
    }else{
      state.hasSqftById.set(l.id, flagged);
    }
  }
  renderListings(); if(state.listings.length) await loadListing(state.listings[0].id);
}
init().catch(err=>{ setStatus(String(err)); console.error(err); });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "curate_web/0.1"

    @property
    def app(self) -> "App":  # type: ignore[name-defined]
        return self.server.app  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, obj: object) -> None:
        self._send(status, _json(obj), content_type="application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/":
                return self._send(HTTPStatus.OK, INDEX_HTML.encode("utf-8"), content_type="text/html; charset=utf-8")
            if path == "/api/meta":
                return self._send_json(HTTPStatus.OK, {"dataset": str(self.app.cfg.dataset_path), "out_dir": str(self.app.cfg.out_dir)})
            if path == "/api/listings":
                return self._send_json(HTTPStatus.OK, {"listings": self.app.listings_public()})
            if path == "/api/listing":
                listing_id = (qs.get("id") or [""])[0]
                return self._send_json(HTTPStatus.OK, self.app.listing_payload(listing_id))
            if path == "/thumb":
                rel = _safe_relpath((qs.get("path") or [""])[0])
                w = int((qs.get("w") or ["320"])[0])
                w = max(64, min(640, w))
                src = self.app.cfg.dataset_root / rel
                if not src.exists():
                    return self._send_json(HTTPStatus.NOT_FOUND, {"error": f"missing file: {rel}"})
                dst = _thumb_path_for(rel, w)
                if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                    _make_thumb(src, dst, width=w)
                return self._send_file(dst)
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as e:
            return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    def _send_file(self, path: Path) -> None:
        ctype, _ = mimetypes.guess_type(str(path))
        if not ctype:
            ctype = "application/octet-stream"
        body = path.read_bytes()
        return self._send(HTTPStatus.OK, body, content_type=ctype)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/export", "/api/export_many"}:
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n)
            payload = json.loads(raw.decode("utf-8"))
            if parsed.path == "/api/export":
                out = self.app.export_listing(payload)
            else:
                out = self.app.export_many(payload)
            return self._send_json(HTTPStatus.OK, out)
        except Exception as e:
            return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))


class App:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.dataset_root, self._listings = _load_dataset(cfg.dataset_path)
        self._by_id = {str(l.get("id") or "").strip(): l for l in self._listings if str(l.get("id") or "").strip()}
        cfg.out_dir.mkdir(parents=True, exist_ok=True)

    def listings_public(self) -> list[dict[str, object]]:
        out = []
        for l in self._listings:
            listing_id = str(l.get("id") or "").strip()
            if not listing_id:
                continue
            sqft = l.get("sqft", None)
            if not isinstance(sqft, (int, float)):
                sqft = None
            out.append(
                {
                    "id": listing_id,
                    "url": l.get("url"),
                    "has_sqft_data": bool(l.get("has_sqft_data")) if "has_sqft_data" in l else False,
                    "sqft": sqft,
                    "photo_count": int(l.get("photo_count") or 0),
                }
            )
        return out

    def listing_payload(self, listing_id: str) -> dict[str, object]:
        listing_id = str(listing_id or "").strip()
        l = self._by_id.get(listing_id)
        if not l:
            raise ValueError(f"Unknown listing_id: {listing_id}")
        photo_paths = _list_listing_photos(self.cfg.dataset_root, l)
        return {
            "id": listing_id,
            "url": l.get("url"),
            "has_sqft_data": bool(l.get("has_sqft_data")) if "has_sqft_data" in l else False,
            "sqft": l.get("sqft"),
            "photo_paths": photo_paths,
            "excluded": [],
        }

    def _export_dataset_path(self) -> Path:
        return self.cfg.out_dir / "listings.json"

    def _load_export_dataset(self) -> dict[str, object]:
        p = self._export_dataset_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {
            "dataset_info": {
                "name": "streeteasy_clean_set",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "source_dataset": str(self.cfg.dataset_path),
                "notes": "Exported by sample-collection/scripts/curate_web.py",
            },
            "listings": [],
        }

    def _write_export_dataset(self, obj: dict[str, object]) -> None:
        self._export_dataset_path().write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def export_listing(self, payload: dict[str, object]) -> dict[str, object]:
        listing_id = str(payload.get("listing_id") or "").strip()
        if not listing_id:
            raise ValueError("missing listing_id")
        l = self._by_id.get(listing_id)
        if not l:
            raise ValueError(f"Unknown listing_id: {listing_id}")

        excluded = payload.get("excluded", [])
        if not isinstance(excluded, list) or not all(isinstance(p, str) for p in excluded):
            raise ValueError("excluded must be a list[str]")
        excluded_set = {_safe_relpath(p) for p in excluded}

        sqft = payload.get("sqft", None)
        if sqft is not None and not isinstance(sqft, (int, float)):
            raise ValueError("sqft must be a number or null")

        all_paths = _list_listing_photos(self.cfg.dataset_root, l)
        kept = [p for p in all_paths if p not in excluded_set]
        if not kept:
            raise ValueError("No photos selected (everything excluded).")

        out_photos_dir = self.cfg.out_dir / "photos" / listing_id
        if out_photos_dir.exists():
            shutil.rmtree(out_photos_dir)
        out_photos_dir.mkdir(parents=True, exist_ok=True)
        missing: list[str] = []
        for i, rel in enumerate(kept):
            src = self.cfg.dataset_root / rel
            if not src.exists():
                missing.append(rel)
                continue
            dst = out_photos_dir / f"photo_{i:02d}{src.suffix.lower()}"
            shutil.copy2(src, dst)

        if missing:
            # Fail fast instead of silently exporting empty/partial listings.
            # This usually means the underlying dataset references photos that were never downloaded.
            raise ValueError(
                f"Missing {len(missing)}/{len(kept)} selected photos on disk. "
                f"Example missing paths: {missing[:5]}. "
                "Make sure the source dataset's photos are downloaded before exporting."
            )

        export_obj = self._load_export_dataset()
        listings = export_obj.get("listings", [])
        if not isinstance(listings, list):
            listings = []

        kept_rel = [f"photos/{listing_id}/{p.name}" for p in sorted(out_photos_dir.iterdir()) if p.is_file()]
        entry = {
            "id": listing_id,
            "url": l.get("url"),
            "has_sqft_data": bool(sqft is not None),
            "sqft": float(sqft) if isinstance(sqft, (int, float)) else None,
            "photo_count": len(kept_rel),
            "photo_paths": kept_rel,
        }

        listings = [x for x in listings if not (isinstance(x, dict) and x.get("id") == listing_id)]
        listings.append(entry)
        export_obj["listings"] = sorted(listings, key=lambda x: str(x.get("id", "")) if isinstance(x, dict) else "")
        self._write_export_dataset(export_obj)

        return {"ok": True, "listing_id": listing_id, "n_selected": len(kept_rel), "out_dir": str(self.cfg.out_dir)}

    def export_many(self, payload: dict[str, object]) -> dict[str, object]:
        items = payload.get("items", [])
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a non-empty list")
        results = []
        ok = 0
        for it in items:
            if not isinstance(it, dict):
                results.append({"ok": False, "error": "bad_item"})
                continue
            try:
                res = self.export_listing(it)
                results.append(res)
                ok += 1
            except Exception as e:
                results.append({"ok": False, "listing_id": it.get("listing_id"), "error": str(e)})
        return {"ok": True, "n": len(items), "ok_count": ok, "out_dir": str(self.cfg.out_dir), "results": results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True, help="Path to listings.json (with listings + photos/*).")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for the exported clean set (default: <dataset_dir>/../clean_set_export).",
    )
    ap.add_argument("--host", type=str, default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()

    dataset_path: Path = args.dataset
    if not dataset_path.exists():
        raise SystemExit(f"Missing dataset: {dataset_path}")

    out_dir = args.out or (dataset_path.parent.parent / "clean_set_export")
    out_dir = out_dir.resolve()

    dataset_root, _ = _load_dataset(dataset_path)
    cfg = AppConfig(
        dataset_path=dataset_path.resolve(),
        dataset_root=dataset_root.resolve(),
        out_dir=out_dir,
        port=int(args.port),
        host=str(args.host),
    )

    app = App(cfg)

    httpd = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    httpd.app = app  # type: ignore[attr-defined]

    url = f"http://{cfg.host}:{cfg.port}/"
    print("OPEN:", url)
    print("DATASET:", cfg.dataset_path)
    print("EXPORT:", cfg.out_dir)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
