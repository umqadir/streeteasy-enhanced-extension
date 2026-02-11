#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class VolumeLayout:
    root: Path

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def vendor_dir(self) -> Path:
        return self.models_dir / "vendor"

    @property
    def checkpoints_dir(self) -> Path:
        return self.models_dir / "checkpoints"


def _default_volume_root() -> Path:
    env = os.environ.get("CVP_VOLUME")
    if env:
        return Path(env)
    for candidate in ("/runpod-volume", "/workspace"):
        p = Path(candidate)
        if p.exists():
            return p
    return Path.home() / ".cache" / "cv_pipeline"


def _setup_cache_env(volume: VolumeLayout) -> None:
    volume.models_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TORCH_HOME", str(volume.models_dir / "torch"))
    os.environ.setdefault("HF_HOME", str(volume.models_dir / "hf"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(volume.models_dir / "hf" / "transformers"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(volume.models_dir / "hf" / "hub"))


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _git_clone_or_update(url: str, dest: Path, *, recursive: bool = False) -> dict[str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _run(["git", "-C", str(dest), "fetch", "--all", "--tags"])
        try:
            _run(["git", "-C", str(dest), "pull", "--ff-only"])
        except Exception:
            # Detached HEAD or local changes; keep as-is, but fetch completed.
            pass
        if recursive:
            _run(["git", "-C", str(dest), "submodule", "update", "--init", "--recursive"])
    else:
        cmd = ["git", "clone", "--depth", "1"]
        if recursive:
            cmd += ["--recurse-submodules", "--shallow-submodules"]
        cmd += [url, str(dest)]
        _run(cmd)

    head = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    return {"url": url, "path": str(dest), "head": head}


def _head_content_length(url: str) -> int | None:
    req = Request(url, method="HEAD")
    try:
        with urlopen(req, timeout=30) as r:
            raw = r.headers.get("Content-Length")
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _download(url: str, out_path: Path, *, retries: int, timeout_s: float) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    expected_size = _head_content_length(url)
    max_tries = max(1, int(retries))
    timeout = max(30.0, float(timeout_s))
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    for attempt in range(1, max_tries + 1):
        try:
            if tmp.exists():
                tmp.unlink()
            written = 0
            with urlopen(url, timeout=timeout) as r, tmp.open("wb") as f:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    f.write(chunk)
            if written <= 0:
                raise RuntimeError("download produced 0 bytes")
            if expected_size is not None and written != expected_size:
                raise RuntimeError(
                    f"size mismatch: wrote {written} bytes, expected {expected_size} bytes (from Content-Length)"
                )
            tmp.replace(out_path)
            return
        except Exception as e:
            if tmp.exists():
                tmp.unlink()
            if attempt >= max_tries:
                raise RuntimeError(f"Failed download after {max_tries} attempts: {url} -> {out_path}: {e}") from e
            wait_s = min(60, 2**attempt)
            print(f"download retry {attempt}/{max_tries - 1} in {wait_s}s: {url} ({type(e).__name__}: {e})")
            time.sleep(wait_s)


def _manifest_path(volume: VolumeLayout) -> Path:
    return volume.models_dir / "manifest.json"


def _load_manifest(volume: VolumeLayout) -> dict[str, object]:
    path = _manifest_path(volume)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"volume_root": str(volume.root), "vendor": [], "downloads": [], "notes": []}


def _write_manifest(volume: VolumeLayout, manifest: dict[str, object]) -> None:
    _manifest_path(volume).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _record_vendor(manifest: dict[str, object], name: str, entry: dict[str, str]) -> None:
    entry = dict(entry)
    entry["name"] = name
    vendors: list[dict[str, str]] = list(manifest.get("vendor", []))  # type: ignore[assignment]
    vendors = [v for v in vendors if v.get("name") != name]
    vendors.append(entry)
    manifest["vendor"] = sorted(vendors, key=lambda v: v.get("name", ""))


def _record_download(manifest: dict[str, object], name: str, url: str, path: Path) -> None:
    downloads: list[dict[str, str]] = list(manifest.get("downloads", []))  # type: ignore[assignment]
    path_str = str(path)
    downloads = [d for d in downloads if not (d.get("name") == name and d.get("path") == path_str and d.get("url") == url)]
    downloads.append({"name": name, "url": url, "path": path_str})
    manifest["downloads"] = downloads


def _record_note(manifest: dict[str, object], note: str) -> None:
    notes: list[str] = list(manifest.get("notes", []))  # type: ignore[assignment]
    if note not in notes:
        notes.append(note)
    manifest["notes"] = notes


def _fetch_hf_expected_sizes(repo_id: str) -> dict[str, int]:
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo_id=repo_id, files_metadata=True)
    expected: dict[str, int] = {}
    siblings = getattr(info, "siblings", None) or []
    for sibling in siblings:
        name = getattr(sibling, "rfilename", None)
        size = getattr(sibling, "size", None)
        if not isinstance(name, str) or not name:
            continue
        if isinstance(size, int) and size >= 0:
            expected[name] = size
    return expected


def _validate_hf_snapshot(local_dir: Path, *, expected_sizes: dict[str, int]) -> list[str]:
    issues: list[str] = []
    if not local_dir.exists():
        return [f"missing snapshot dir: {local_dir}"]

    missing = [rel for rel in expected_sizes if not (local_dir / rel).exists()]
    if missing:
        show = ", ".join(sorted(missing)[:10])
        suffix = "" if len(missing) <= 10 else f" ... (+{len(missing) - 10} more)"
        issues.append(f"missing files: {show}{suffix}")

    bad_size: list[str] = []
    for rel, expected in expected_sizes.items():
        p = local_dir / rel
        if not p.exists() or not p.is_file():
            continue
        actual = p.stat().st_size
        if actual != expected:
            bad_size.append(f"{rel} ({actual} != {expected})")
    if bad_size:
        show = ", ".join(sorted(bad_size)[:10])
        suffix = "" if len(bad_size) <= 10 else f" ... (+{len(bad_size) - 10} more)"
        issues.append(f"size mismatches: {show}{suffix}")

    incomplete = sorted(local_dir.rglob("*.incomplete"))
    if incomplete:
        show = ", ".join(str(p.relative_to(local_dir)) for p in incomplete[:10])
        suffix = "" if len(incomplete) <= 10 else f" ... (+{len(incomplete) - 10} more)"
        issues.append(f"incomplete files present: {show}{suffix}")

    weight_suffixes = {".pt", ".pth", ".bin", ".safetensors"}
    has_weights = any(p.suffix.lower() in weight_suffixes for p in local_dir.rglob("*") if p.is_file())
    if not has_weights:
        issues.append("no model weight file found (*.pt, *.pth, *.bin, *.safetensors)")
    return issues


def _cmd_vendor_all(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    repos: list[tuple[str, str, str, bool]] = [
        ("depth-anything-v2", "https://github.com/DepthAnything/Depth-Anything-V2.git", "depth-anything-v2", False),
        ("metric3d", "https://github.com/YvanYin/Metric3D.git", "metric3d", False),
        ("unidepth", "https://github.com/lpiccinelli-eth/UniDepth.git", "unidepth", False),
        ("moge", "https://github.com/microsoft/MoGe.git", "moge", False),
        ("lightglue", "https://github.com/cvg/LightGlue.git", "lightglue", False),
        ("dust3r", "https://github.com/naver/dust3r.git", "dust3r", True),
        ("mast3r", "https://github.com/naver/mast3r.git", "mast3r", True),
    ]

    for name, url, dirname, recursive in repos:
        entry = _git_clone_or_update(url, volume.vendor_dir / dirname, recursive=recursive)
        _record_vendor(manifest, name, entry)

    _write_manifest(volume, manifest)
    print("OK: vendor repos ready under", volume.vendor_dir)


def _cmd_depth_anything_metric(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    vendor = _git_clone_or_update(
        "https://github.com/DepthAnything/Depth-Anything-V2.git", volume.vendor_dir / "depth-anything-v2"
    )
    _record_vendor(manifest, "depth-anything-v2", vendor)

    encoder = args.encoder
    dataset = args.dataset
    if dataset not in {"hypersim", "vkitti"}:
        raise SystemExit("--dataset must be hypersim (indoor) or vkitti (outdoor)")

    # Source: Depth Anything V2 metric_depth README (Hugging Face direct resolve URLs).
    ckpt_name = f"depth_anything_v2_metric_{dataset}_{encoder}.pth"
    hf_repo = {
        ("hypersim", "vits"): "Depth-Anything-V2-Metric-Hypersim-Small",
        ("hypersim", "vitb"): "Depth-Anything-V2-Metric-Hypersim-Base",
        ("hypersim", "vitl"): "Depth-Anything-V2-Metric-Hypersim-Large",
        ("vkitti", "vits"): "Depth-Anything-V2-Metric-VKITTI-Small",
        ("vkitti", "vitb"): "Depth-Anything-V2-Metric-VKITTI-Base",
        ("vkitti", "vitl"): "Depth-Anything-V2-Metric-VKITTI-Large",
    }[(dataset, encoder)]

    url = (
        f"https://huggingface.co/depth-anything/{hf_repo}/resolve/main/"
        f"depth_anything_v2_metric_{dataset}_{encoder}.pth?download=true"
    )

    out_path = volume.checkpoints_dir / ckpt_name
    if out_path.exists() and not args.force:
        print(f"skip (exists): {out_path}")
    else:
        print(f"download: {url} -> {out_path}")
        _download(url, out_path, retries=args.retries, timeout_s=args.download_timeout_s)
    _record_download(manifest, "depth-anything-v2-metric", url, out_path)

    _write_manifest(volume, manifest)
    print("OK: wrote", _manifest_path(volume))


def _cmd_metric3d(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    vendor = _git_clone_or_update("https://github.com/YvanYin/Metric3D.git", volume.vendor_dir / "metric3d")
    _record_vendor(manifest, "metric3d", vendor)

    # Source: yvanyin/metric3d hubconf.py (Hugging Face URLs).
    ckpt_urls = {
        "vit_small": "https://huggingface.co/JUGGHM/Metric3D/resolve/main/metric_depth_vit_small_800k.pth",
        "vit_large": "https://huggingface.co/JUGGHM/Metric3D/resolve/main/metric_depth_vit_large_800k.pth",
        "vit_giant2": "https://huggingface.co/JUGGHM/Metric3D/resolve/main/metric_depth_vit_giant2_800k.pth",
        "convtiny_v1": "https://huggingface.co/JUGGHM/Metric3D/resolve/main/convtiny_hourglass_v1.pth",
        "convlarge_v1_1": "https://huggingface.co/JUGGHM/Metric3D/resolve/main/convlarge_hourglass_0.3_150_step750k_v1.1.pth",
    }
    name = args.model
    url = ckpt_urls[name]
    out_path = volume.checkpoints_dir / "metric3d" / Path(url).name
    if out_path.exists() and not args.force:
        print(f"skip (exists): {out_path}")
    else:
        print(f"download: {url} -> {out_path}")
        _download(url, out_path, retries=args.retries, timeout_s=args.download_timeout_s)
    _record_download(manifest, f"metric3d:{name}", url, out_path)
    _record_note(
        manifest,
        "Metric3D inference via torch.hub may require extra deps (mmengine/mmcv). "
        "If torch.hub load fails, install mmengine (and/or mmcv) in your RunPod env.",
    )

    _write_manifest(volume, manifest)
    print("OK: wrote", _manifest_path(volume))


def _cmd_dust3r(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    vendor = _git_clone_or_update("https://github.com/naver/dust3r.git", volume.vendor_dir / "dust3r", recursive=True)
    _record_vendor(manifest, "dust3r", vendor)

    # Source: naver/dust3r README checkpoint table.
    ckpts = {
        "vitl_224_linear": "https://download.europe.naverlabs.com/ComputerVision/DUSt3R/DUSt3R_ViTLarge_BaseDecoder_224_linear.pth",
        "vitl_512_linear": "https://download.europe.naverlabs.com/ComputerVision/DUSt3R/DUSt3R_ViTLarge_BaseDecoder_512_linear.pth",
        "vitl_512_dpt": "https://download.europe.naverlabs.com/ComputerVision/DUSt3R/DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth",
    }
    name = args.model
    url = ckpts[name]
    out_path = volume.checkpoints_dir / "dust3r" / Path(url).name
    if out_path.exists() and not args.force:
        print(f"skip (exists): {out_path}")
    else:
        print(f"download: {url} -> {out_path}")
        _download(url, out_path, retries=args.retries, timeout_s=args.download_timeout_s)
    _record_download(manifest, f"dust3r:{name}", url, out_path)
    _record_note(
        manifest,
        "DUSt3R may require building optional CUDA extensions for speed. "
        "See vendor/dust3r README (croco/models/curope).",
    )

    _write_manifest(volume, manifest)
    print("OK: wrote", _manifest_path(volume))


def _cmd_mast3r(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    vendor = _git_clone_or_update("https://github.com/naver/mast3r.git", volume.vendor_dir / "mast3r", recursive=True)
    _record_vendor(manifest, "mast3r", vendor)

    # Source: naver/mast3r README checkpoint section.
    main_url = "https://download.europe.naverlabs.com/ComputerVision/MASt3R/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
    main_path = volume.checkpoints_dir / "mast3r" / Path(main_url).name
    if main_path.exists() and not args.force:
        print(f"skip (exists): {main_path}")
    else:
        print(f"download: {main_url} -> {main_path}")
        _download(main_url, main_path, retries=args.retries, timeout_s=args.download_timeout_s)
    _record_download(manifest, "mast3r:main", main_url, main_path)

    if args.with_retrieval:
        retr_url = "https://download.europe.naverlabs.com/ComputerVision/MASt3R/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric_retrieval_trainingfree.pth"
        cb_url = "https://download.europe.naverlabs.com/ComputerVision/MASt3R/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric_retrieval_codebook.pkl"
        for url in (retr_url, cb_url):
            out_path = volume.checkpoints_dir / "mast3r" / Path(url).name
            if out_path.exists() and not args.force:
                print(f"skip (exists): {out_path}")
            else:
                print(f"download: {url} -> {out_path}")
                _download(url, out_path, retries=args.retries, timeout_s=args.download_timeout_s)
            _record_download(manifest, "mast3r:retrieval", url, out_path)

    _record_note(
        manifest,
        "MASt3R checkpoints are CC BY-NC-SA 4.0 and may include additional dataset license requirements. "
        "See vendor/mast3r CHECKPOINTS_NOTICE before use.",
    )

    _write_manifest(volume, manifest)
    print("OK: wrote", _manifest_path(volume))


def _cmd_hf_snapshot(args: argparse.Namespace, *, namespace: str) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    try:
        from huggingface_hub import snapshot_download
    except Exception as e:  # pragma: no cover
        raise SystemExit("Missing dependency: huggingface_hub. Run `uv sync` (cv-pipeline).") from e

    local_dir = volume.checkpoints_dir / namespace / args.repo.replace("/", "__")
    expected_sizes = _fetch_hf_expected_sizes(args.repo)
    max_tries = max(1, int(args.retries))
    for attempt in range(1, max_tries + 1):
        run_download = not local_dir.exists() or args.force
        if run_download:
            print(f"snapshot_download: {args.repo} -> {local_dir} (attempt {attempt}/{max_tries})")
            snapshot_download(
                repo_id=args.repo,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False,
                resume_download=True,
                force_download=bool(args.force),
                etag_timeout=float(args.etag_timeout_s),
                max_workers=int(args.hf_max_workers),
            )
        else:
            print(f"validate existing snapshot: {local_dir}")

        issues = _validate_hf_snapshot(local_dir, expected_sizes=expected_sizes)
        if not issues:
            break
        if attempt >= max_tries:
            joined = "\n  - ".join(issues)
            raise RuntimeError(f"Snapshot validation failed for {args.repo}:\n  - {joined}")
        print(f"snapshot validation failed (attempt {attempt}/{max_tries}): {'; '.join(issues)}")
        print("retrying with force_download=True")
        args.force = True
        time.sleep(min(60, 2**attempt))

    _record_download(manifest, f"{namespace}:{args.repo}", f"hf://{args.repo}", local_dir)
    _write_manifest(volume, manifest)
    print("OK: wrote", _manifest_path(volume))


def _cmd_unidepth(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    vendor = _git_clone_or_update("https://github.com/lpiccinelli-eth/UniDepth.git", volume.vendor_dir / "unidepth")
    _record_vendor(manifest, "unidepth", vendor)
    _write_manifest(volume, manifest)

    _cmd_hf_snapshot(args, namespace="unidepth")


def _cmd_moge(args: argparse.Namespace) -> None:
    volume = VolumeLayout(Path(args.volume_root))
    _setup_cache_env(volume)
    manifest = _load_manifest(volume)

    vendor = _git_clone_or_update("https://github.com/microsoft/MoGe.git", volume.vendor_dir / "moge")
    _record_vendor(manifest, "moge", vendor)
    _write_manifest(volume, manifest)

    _cmd_hf_snapshot(args, namespace="moge")


def _cmd_all(args: argparse.Namespace) -> None:
    _cmd_vendor_all(args)
    _cmd_depth_anything_metric(
        argparse.Namespace(
            volume_root=args.volume_root,
            encoder="vitl",
            dataset="hypersim",
            force=args.force,
        )
    )
    _cmd_metric3d(argparse.Namespace(volume_root=args.volume_root, model="vit_small", force=args.force))
    _cmd_metric3d(argparse.Namespace(volume_root=args.volume_root, model="vit_large", force=args.force))
    _cmd_unidepth(argparse.Namespace(volume_root=args.volume_root, repo="lpiccinelli/unidepth-v1-vitl14", force=args.force))
    _cmd_moge(argparse.Namespace(volume_root=args.volume_root, repo="Ruicheng/moge-2-vitl-normal", force=args.force))
    _cmd_dust3r(argparse.Namespace(volume_root=args.volume_root, model="vitl_512_dpt", force=args.force))
    _cmd_mast3r(argparse.Namespace(volume_root=args.volume_root, with_retrieval=True, force=args.force))


def main() -> None:
    parser = argparse.ArgumentParser(description="Download/prepare model assets onto CVP_VOLUME.")
    parser.add_argument("--volume-root", type=Path, default=_default_volume_root())
    parser.add_argument("--force", action="store_true", help="Re-download even if files already exist.")
    parser.add_argument("--retries", type=int, default=3, help="Retries for download/snapshot operations.")
    parser.add_argument("--download-timeout-s", type=float, default=120.0, help="Socket timeout for direct URL downloads.")
    parser.add_argument("--etag-timeout-s", type=float, default=30.0, help="HF metadata timeout in seconds.")
    parser.add_argument("--hf-max-workers", type=int, default=8, help="HF snapshot concurrent workers.")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_vendor = sub.add_parser("vendor-all", help="Clone/update all third-party repos under CVP_VOLUME/models/vendor.")
    p_vendor.set_defaults(func=_cmd_vendor_all)

    p_da = sub.add_parser("depth-anything-metric", help="Depth Anything V2 metric depth (Hypersim/VKITTI).")
    p_da.add_argument("--encoder", choices=["vits", "vitb", "vitl"], default="vitl")
    p_da.add_argument("--dataset", choices=["hypersim", "vkitti"], default="hypersim")
    p_da.set_defaults(func=_cmd_depth_anything_metric)

    p_m3d = sub.add_parser("metric3d", help="Download Metric3D checkpoints (weights only).")
    p_m3d.add_argument(
        "--model",
        choices=["vit_small", "vit_large", "vit_giant2", "convtiny_v1", "convlarge_v1_1"],
        default="vit_small",
    )
    p_m3d.set_defaults(func=_cmd_metric3d)

    p_d3r = sub.add_parser("dust3r", help="Clone DUSt3R + download checkpoint.")
    p_d3r.add_argument("--model", choices=["vitl_224_linear", "vitl_512_linear", "vitl_512_dpt"], default="vitl_512_dpt")
    p_d3r.set_defaults(func=_cmd_dust3r)

    p_ma = sub.add_parser("mast3r", help="Clone MASt3R + download checkpoint(s).")
    p_ma.add_argument("--with-retrieval", action="store_true", help="Also download retrieval weights/codebook.")
    p_ma.set_defaults(func=_cmd_mast3r)

    p_ud = sub.add_parser("unidepth", help="Clone UniDepth repo + snapshot a UniDepth model repo from Hugging Face.")
    p_ud.add_argument("--repo", default="lpiccinelli/unidepth-v1-vitl14")
    p_ud.set_defaults(func=_cmd_unidepth)

    p_moge = sub.add_parser("moge", help="Clone MoGe repo + snapshot a MoGe model repo from Hugging Face.")
    p_moge.add_argument("--repo", default="Ruicheng/moge-2-vitl-normal")
    p_moge.set_defaults(func=_cmd_moge)

    p_all = sub.add_parser("all", help="Download a full recommended set (large).")
    p_all.set_defaults(func=_cmd_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
