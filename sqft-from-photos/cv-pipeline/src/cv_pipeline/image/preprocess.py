from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy.ndimage import laplace


@dataclass(frozen=True)
class PreprocessResult:
    kept_images: list[Path]
    dropped_images: list[Path]
    mapping: dict[str, str]  # original -> processed
    diagnostics: dict[str, object]


def list_images(images_dir: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted([p for p in images_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts])


def _load_rgb(path: Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _resize_to_max_side(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_side:
        return img
    scale = max_side / float(longest)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _a_hash(img: Image.Image, size: int = 8) -> int:
    g = img.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    arr = np.asarray(g, dtype=np.float32)
    mean = float(arr.mean())
    bits = (arr > mean).astype(np.uint8).ravel()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def _hamming(a: int, b: int) -> int:
    return int((a ^ b).bit_count())


def _blur_score(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    return float(np.var(laplace(g)))


def _pick_best(paths: list[Path], info: dict[Path, dict[str, float]]) -> Path:
    def key(p: Path) -> tuple[float, float]:
        meta = info[p]
        return (meta["pixels"], meta["blur"])

    return max(paths, key=key)


def preprocess_images(
    images_dir: Path,
    out_dir: Path,
    *,
    max_side: int = 1600,
    dedup_hamming_threshold: int = 4,
    image_paths: list[Path] | None = None,
) -> PreprocessResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_images = list(image_paths) if image_paths is not None else list_images(images_dir)
    mapping: dict[str, str] = {}
    info: dict[Path, dict[str, float]] = {}

    processed_paths: list[Path] = []
    for src in all_images:
        img = _load_rgb(src)
        img = _resize_to_max_side(img, max_side=max_side)

        dst = out_dir / src.name
        if dst.suffix.lower() not in {".jpg", ".jpeg"}:
            dst = dst.with_suffix(".jpg")
        img.save(dst, format="JPEG", quality=92, optimize=True)
        mapping[str(src)] = str(dst)

        w, h = img.size
        info[dst] = {"pixels": float(w * h), "blur": _blur_score(img), "hash": _a_hash(img)}
        processed_paths.append(dst)

    # Deduplicate (O(n^2), fine for listing sizes).
    hashes = {p: int(info[p]["hash"]) for p in processed_paths}
    parent: dict[Path, Path] = {p: p for p in processed_paths}

    def find(x: Path) -> Path:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: Path, b: Path) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(processed_paths):
        for b in processed_paths[i + 1 :]:
            if _hamming(hashes[a], hashes[b]) <= dedup_hamming_threshold:
                union(a, b)

    groups: dict[Path, list[Path]] = {}
    for p in processed_paths:
        groups.setdefault(find(p), []).append(p)

    kept: list[Path] = []
    dropped: list[Path] = []
    for _, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        best = _pick_best(group, info=info)
        kept.append(best)
        dropped.extend([p for p in group if p != best])

    kept = sorted(kept)
    dropped = sorted(dropped)

    diagnostics = {
        "input_images": len(all_images),
        "processed_images": len(processed_paths),
        "kept_images": len(kept),
        "dropped_images": len(dropped),
        "dedup_hamming_threshold": dedup_hamming_threshold,
        "max_side": max_side,
    }
    return PreprocessResult(
        kept_images=kept,
        dropped_images=dropped,
        mapping=mapping,
        diagnostics=diagnostics,
    )
