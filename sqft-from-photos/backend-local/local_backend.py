#!/usr/bin/env python3
"""SleepEasy local backend for room sqft estimation.

Runs fully local on the host machine and exposes simple HTTP endpoints consumed
by the Chrome extension service worker.

Endpoints:
  GET  /health
  GET  /backend/config
  POST /backend/config
  POST /estimate/single
  POST /estimate/multi

Request payloads:
  /estimate/single: {"imageUrl": "https://..."} or {"imagePath": "/abs/path.jpg"}
  /estimate/multi:  {"imageUrls": ["https://...", ...]} or {"imagePaths": ["/abs/path.jpg", ...]}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = Path(__file__).resolve().parent
V2_PIPELINE_DIR = REPO_ROOT / "v2-pipeline"
# Keep runtime artifacts under backend-local/ to avoid creating sibling folders.
RUNS_ROOT = BACKEND_ROOT / ".runtime" / "runs"
RUNS_ROOT.mkdir(parents=True, exist_ok=True)

# Ensure v2 pipeline module can be imported when this script is run from repo root.
import sys

if str(V2_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(V2_PIPELINE_DIR))

import estimate_v2b as v2b  # noqa: E402


INFER_LOCK = threading.Lock()
_ORIG_CUDA_IS_AVAILABLE = None
_ORIG_MPS_IS_AVAILABLE = None


@dataclass
class BackendConfig:
    device_policy: str = "auto"  # auto | cpu | mps
    analysis_mode: str = "auto"  # auto | single-image
    dust3r_niter: int = 120
    save_debug_artifacts: bool = False


STATE = BackendConfig()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_json(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_len = handler.headers.get("Content-Length", "0").strip()
    length = int(raw_len) if raw_len else 0
    body = handler.rfile.read(length) if length > 0 else b"{}"
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _round1(x: float) -> float:
    return float(round(float(x), 1))


def _confidence_from_ci(sqft: float, lo: float, hi: float) -> float:
    width = max(0.0, float(hi) - float(lo))
    denom = max(float(sqft), 1.0)
    rel = width / denom
    conf = 1.0 - rel
    return max(0.05, min(0.99, conf))


def _reset_model_state() -> None:
    # Reset lazily-loaded model handles so next request reloads with current policy.
    for attr in (
        "_segformer_model",
        "_segformer_processor",
        "_moge_model",
        "_dust3r_model",
        "_moge_device",
        "_dust3r_device",
    ):
        if hasattr(v2b, attr):
            setattr(v2b, attr, None)


def _capture_probe_functions() -> None:
    import torch

    global _ORIG_CUDA_IS_AVAILABLE, _ORIG_MPS_IS_AVAILABLE  # noqa: PLW0603
    if _ORIG_CUDA_IS_AVAILABLE is None:
        _ORIG_CUDA_IS_AVAILABLE = torch.cuda.is_available
    if hasattr(torch.backends, "mps") and _ORIG_MPS_IS_AVAILABLE is None:
        _ORIG_MPS_IS_AVAILABLE = torch.backends.mps.is_available


def _restore_probe_functions() -> None:
    import torch

    if _ORIG_CUDA_IS_AVAILABLE is not None:
        torch.cuda.is_available = _ORIG_CUDA_IS_AVAILABLE  # type: ignore[assignment]
    if hasattr(torch.backends, "mps") and _ORIG_MPS_IS_AVAILABLE is not None:
        torch.backends.mps.is_available = _ORIG_MPS_IS_AVAILABLE  # type: ignore[attr-defined, assignment]


def _apply_device_policy(policy: str) -> None:
    import torch

    if policy not in {"auto", "cpu", "mps"}:
        raise ValueError("device_policy must be one of: auto, cpu, mps")

    _capture_probe_functions()
    _restore_probe_functions()

    if policy == "auto":
        return

    # Monkeypatch backend probes used by v2 pipeline loaders.
    if policy == "cpu":
        torch.cuda.is_available = lambda: False  # type: ignore[assignment]
        if hasattr(torch.backends, "mps"):
            torch.backends.mps.is_available = lambda: False  # type: ignore[attr-defined, assignment]
        return

    # policy == "mps"
    has_mps = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
    if not has_mps:
        raise RuntimeError("MPS requested but torch.backends.mps.is_available() is False on this machine.")
    torch.cuda.is_available = lambda: False  # type: ignore[assignment]


def _device_info() -> dict[str, Any]:
    return {
        "policy": STATE.device_policy,
        "moge_device": getattr(v2b, "_moge_device", None),
        "dust3r_device": getattr(v2b, "_dust3r_device", None),
        "segformer_device": (
            str(getattr(v2b, "_segformer_model").device)
            if getattr(v2b, "_segformer_model", None) is not None
            else None
        ),
        "models_loaded": {
            "segformer": getattr(v2b, "_segformer_model", None) is not None,
            "moge": getattr(v2b, "_moge_model", None) is not None,
            "dust3r": getattr(v2b, "_dust3r_model", None) is not None,
        },
    }


def _system_capabilities() -> dict[str, bool]:
    import torch

    _capture_probe_functions()
    cuda_probe = _ORIG_CUDA_IS_AVAILABLE or torch.cuda.is_available
    mps_probe = _ORIG_MPS_IS_AVAILABLE
    if mps_probe is None and hasattr(torch.backends, "mps"):
        mps_probe = torch.backends.mps.is_available

    try:
        cuda_ok = bool(cuda_probe())
    except Exception:  # noqa: BLE001
        cuda_ok = False

    try:
        mps_ok = bool(mps_probe()) if mps_probe is not None else False
    except Exception:  # noqa: BLE001
        mps_ok = False

    return {
        "cudaAvailable": cuda_ok,
        "mpsAvailable": mps_ok,
    }


def _recommended_analysis_mode(caps: dict[str, bool]) -> str:
    return "auto" if bool(caps.get("cudaAvailable")) else "single-image"


def _public_config() -> dict[str, Any]:
    return {
        "mode": "local",
        "devicePolicy": STATE.device_policy,
        "analysisMode": STATE.analysis_mode,
        "dust3rNiter": int(STATE.dust3r_niter),
        "saveDebugArtifacts": bool(STATE.save_debug_artifacts),
    }


def _download_image(url: str, dst: Path) -> Path:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://streeteasy.com/",
        },
    )
    with urlopen(req, timeout=45) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "image" not in ctype:
            raise RuntimeError(f"URL did not return image content-type: {ctype or 'unknown'}")
        data = resp.read()
    if not data:
        raise RuntimeError("Downloaded image payload is empty.")
    dst.write_bytes(data)
    return dst


def _stage_images(payload: dict[str, Any], temp_dir: Path) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    refs: list[str] = []

    if "imagePath" in payload:
        p = Path(str(payload["imagePath"])).expanduser().resolve()
        if not p.exists():
            raise RuntimeError(f"imagePath does not exist: {p}")
        return [p], [str(p)]

    if "imagePaths" in payload:
        in_paths = [Path(str(x)).expanduser().resolve() for x in payload.get("imagePaths") or []]
        if not in_paths:
            raise RuntimeError("imagePaths is empty")
        for p in in_paths:
            if not p.exists():
                raise RuntimeError(f"imagePath does not exist: {p}")
        return in_paths, [str(p) for p in in_paths]

    if "imageUrl" in payload:
        u = str(payload["imageUrl"]).strip()
        if not u:
            raise RuntimeError("imageUrl is empty")
        ext = Path(urlparse(u).path).suffix.lower() or ".jpg"
        dst = temp_dir / f"img_00{ext}"
        return [_download_image(u, dst)], [u]

    if "imageUrls" in payload:
        urls = [str(u).strip() for u in (payload.get("imageUrls") or []) if str(u).strip()]
        if not urls:
            raise RuntimeError("imageUrls is empty")
        for i, u in enumerate(urls):
            ext = Path(urlparse(u).path).suffix.lower() or ".jpg"
            dst = temp_dir / f"img_{i:02d}{ext}"
            paths.append(_download_image(u, dst))
            refs.append(u)
        return paths, refs

    raise RuntimeError("Request must include one of: imageUrl, imageUrls, imagePath, imagePaths")


def _run_estimate(image_paths: list[Path], *, method: str) -> dict[str, Any]:
    assert method in {"single-image", "dust3r-scene"}

    debug_dir = None
    if STATE.save_debug_artifacts:
        debug_dir = RUNS_ROOT / f"run_{_ts()}_{method.replace('-', '_')}"
        debug_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    with INFER_LOCK:
        result = v2b.run_pipeline(
            image_paths,
            interactive=False,
            debug_dir=debug_dir,
            impute_room_corners=False,
            multiview_method=method,
            dust3r_niter=int(STATE.dust3r_niter),
        )
    dt_ms = int(round((time.time() - t0) * 1000.0))

    sqft = float(result.sqft)
    lo = float(result.ci_lo)
    hi = float(result.ci_hi)
    conf = _confidence_from_ci(sqft, lo, hi)
    pipeline = "single" if method == "single-image" else "multi"

    return {
        "estimatedSqft": int(round(sqft)),
        "estimatedSqftFloat": sqft,
        "confidence": round(conf, 3),
        "apiVersion": "v2-local",
        "pipeline": pipeline,
        "method": result.method,
        "ci": {"lo": lo, "hi": hi},
        "runtimeMs": dt_ms,
        "photosUsed": len(image_paths),
        "device": _device_info(),
    }


def _resolve_multiview_method(
    *,
    n_images: int,
    requested_method: str | None,
) -> str:
    if n_images <= 1:
        return "single-image"

    if requested_method is not None:
        method = requested_method.strip().lower()
        if method not in {"dust3r-scene", "single-image"}:
            raise RuntimeError("multiviewMethod must be dust3r-scene or single-image")
        return method

    if STATE.analysis_mode == "single-image":
        return "single-image"
    return "dust3r-scene"


class Handler(BaseHTTPRequestHandler):
    server_version = "SleepEasyLocalBackend/0.1"

    def do_OPTIONS(self) -> None:
        _safe_json(self, HTTPStatus.NO_CONTENT, {"ok": True})

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                caps = _system_capabilities()
                _safe_json(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "mode": "local",
                        "apiVersion": "v2-local",
                        "device": _device_info(),
                        "capabilities": caps,
                        "analysisMode": STATE.analysis_mode,
                        "recommendation": {
                            "analysisMode": _recommended_analysis_mode(caps),
                        },
                        "dust3rNiter": STATE.dust3r_niter,
                    },
                )
                return
            if self.path == "/backend/config":
                _safe_json(self, HTTPStatus.OK, {"ok": True, "config": _public_config()})
                return
            _safe_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except Exception as ex:  # noqa: BLE001
            _safe_json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(ex)})

    def do_POST(self) -> None:
        try:
            payload = _read_json(self)
            if self.path == "/backend/config":
                self._handle_config(payload)
                return
            if self.path == "/estimate/single":
                self._handle_estimate_single(payload)
                return
            if self.path == "/estimate/multi":
                self._handle_estimate_multi(payload)
                return
            _safe_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except Exception as ex:  # noqa: BLE001
            _safe_json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(ex)})

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        # Keep terminal output concise.
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _handle_config(self, payload: dict[str, Any]) -> None:
        mode = payload.get("mode", "local")
        device_policy = payload.get("devicePolicy", STATE.device_policy)
        analysis_mode = payload.get("analysisMode", STATE.analysis_mode)
        niter = int(payload.get("dust3rNiter", STATE.dust3r_niter))
        save_debug = bool(payload.get("saveDebugArtifacts", STATE.save_debug_artifacts))

        if mode != "local":
            raise RuntimeError("mode must be local for this release")

        device_policy = str(device_policy).lower()
        if device_policy not in {"auto", "cpu", "mps"}:
            raise RuntimeError("devicePolicy must be auto, cpu, or mps")

        analysis_mode = str(analysis_mode).strip().lower()
        if analysis_mode not in {"auto", "single-image"}:
            raise RuntimeError("analysisMode must be auto or single-image")

        # If policy changes, reset lazy model cache so models reload on target backend.
        if device_policy != STATE.device_policy:
            _reset_model_state()

        STATE.device_policy = device_policy
        STATE.analysis_mode = analysis_mode
        STATE.dust3r_niter = max(20, min(600, niter))
        STATE.save_debug_artifacts = save_debug

        _apply_device_policy(STATE.device_policy)
        _safe_json(self, HTTPStatus.OK, {"ok": True, "config": _public_config()})

    def _handle_estimate_single(self, payload: dict[str, Any]) -> None:
        request_policy = str(payload.get("devicePolicy") or STATE.device_policy).lower()
        if request_policy != STATE.device_policy:
            # Keep service-level policy authoritative; clients update via /backend/config.
            request_policy = STATE.device_policy

        _apply_device_policy(request_policy)
        temp_dir = Path(tempfile.mkdtemp(prefix="sqft_single_"))
        try:
            image_paths, refs = _stage_images(payload, temp_dir)
            if len(image_paths) != 1:
                raise RuntimeError("/estimate/single expects exactly one image")
            out = _run_estimate(image_paths, method="single-image")
            out["request"] = {"imageUrl": refs[0]}
            _safe_json(self, HTTPStatus.OK, out)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _handle_estimate_multi(self, payload: dict[str, Any]) -> None:
        _apply_device_policy(STATE.device_policy)
        temp_dir = Path(tempfile.mkdtemp(prefix="sqft_multi_"))
        try:
            image_paths, refs = _stage_images(payload, temp_dir)
            method = _resolve_multiview_method(
                n_images=len(image_paths),
                requested_method=payload.get("multiviewMethod"),
            )
            out = _run_estimate(image_paths, method=method)
            out["request"] = {
                "imageUrls": refs,
                "multiviewMethod": method,
            }
            _safe_json(self, HTTPStatus.OK, out)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SleepEasy local sqft backend")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--device-policy", choices=["auto", "cpu", "mps"], default="auto")
    p.add_argument("--analysis-mode", choices=["auto", "single-image"], default="auto")
    p.add_argument("--dust3r-niter", type=int, default=120)
    p.add_argument("--save-debug-artifacts", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    STATE.device_policy = args.device_policy
    STATE.analysis_mode = str(args.analysis_mode).strip().lower()
    STATE.dust3r_niter = max(20, min(600, int(args.dust3r_niter)))
    STATE.save_debug_artifacts = bool(args.save_debug_artifacts)

    _apply_device_policy(STATE.device_policy)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SleepEasy local backend listening on http://{args.host}:{args.port}")
    print(
        "mode=local "
        f"device_policy={STATE.device_policy} "
        f"analysis_mode={STATE.analysis_mode} "
        f"dust3r_niter={STATE.dust3r_niter}"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
