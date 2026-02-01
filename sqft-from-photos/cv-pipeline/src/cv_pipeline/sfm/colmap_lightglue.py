from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.paths import VolumePaths
from cv_pipeline.sfm.colmap_db import CameraSpec, ColmapDatabase
from cv_pipeline.sfm.colmap_model import ColmapModel, load_colmap_model_txt
from cv_pipeline.utils.subprocess import require_binary, run


@dataclass(frozen=True)
class LearnedMatchingConfig:
    extractor: str = "superpoint"  # superpoint|disk|aliked|sift
    max_num_keypoints: int = 2048
    filter_threshold: float = 0.1
    device: str = "cuda"
    batch_size_pairs: int = 1


@dataclass(frozen=True)
class ColmapLearnedRunResult:
    model: ColmapModel
    sparse_model_dir: Path
    sparse_model_txt_dir: Path
    diagnostics: dict[str, object]
    models: dict[str, ColmapModel] | None = None


def _select_best_model_dir(sparse_dir: Path) -> Path:
    candidates = sorted([p for p in sparse_dir.iterdir() if p.is_dir() and p.name.isdigit()])
    if not candidates:
        raise FileNotFoundError(f"No COLMAP models found under: {sparse_dir}")
    # Prefer model with the most registered images.
    best = candidates[0]
    best_score = -1
    for c in candidates:
        images_txt = c / "images.txt"
        if images_txt.exists():
            # images.txt has 2 lines per image
            n = sum(1 for _ in images_txt.open("r", encoding="utf-8")) // 2
            score = int(n)
        else:
            # fallback
            score = int((c / "images.bin").exists()) + int((c / "points3D.bin").exists())
        if score > best_score:
            best_score = score
            best = c
    return best


def _ensure_lightglue_vendor(volume: VolumePaths) -> Path:
    repo = volume.vendor_dir / "lightglue"
    if not repo.exists():
        raise FileNotFoundError(
            f"Missing LightGlue vendor repo at {repo}. "
            "Run: `python cv-pipeline/scripts/download_models.py vendor-all`"
        )
    pkg_root = repo
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))
    return repo


def run_colmap_sfm_lightglue(
    images_dir: Path,
    work_dir: Path,
    *,
    volume: VolumePaths,
    pairs: list[tuple[str, str]],
    camera_model: str = "SIMPLE_RADIAL",
    default_focal_length_factor: float = 1.2,
    single_camera: bool = True,
    matching: LearnedMatchingConfig = LearnedMatchingConfig(),
) -> ColmapLearnedRunResult:
    """
    SfM with COLMAP, but using learned features+matching (LightGlue) instead of COLMAP's exhaustive matcher.

    Pipeline:
    - Build a minimal COLMAP database (cameras/images/keypoints/matches).
    - Run `colmap mapper`.
    - Convert best model to TXT and parse it.
    """
    require_binary("colmap")
    _ensure_lightglue_vendor(volume)

    try:
        import torch
        from lightglue import LightGlue, ALIKED, DISK, SIFT, SuperPoint
        from lightglue.utils import load_image, rbd
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Missing LightGlue dependencies. Install: `cd cv-pipeline && uv sync --extra gpu --extra sfm`."
        ) from e

    work_dir.mkdir(parents=True, exist_ok=True)
    colmap_dir = work_dir / "colmap_lightglue"
    colmap_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = colmap_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    db_path = colmap_dir / "database.db"
    sparse_dir = colmap_dir / "sparse"
    sparse_dir.mkdir(parents=True, exist_ok=True)

    # Build database.
    db = ColmapDatabase(db_path)

    # Camera(s): create 1 shared camera by default (single_camera=True).
    imgs = sorted([p for p in images_dir.iterdir() if p.is_file()])
    if not imgs:
        raise FileNotFoundError(f"No images found in: {images_dir}")

    # Use the first image size as representative (preprocessed images share max_side but may differ).
    from PIL import Image

    w0, h0 = Image.open(imgs[0]).size
    f = float(default_focal_length_factor) * float(max(w0, h0))
    cx, cy = float(w0) / 2.0, float(h0) / 2.0
    k1 = 0.0
    cam_spec = CameraSpec(
        model=camera_model,
        width=int(w0),
        height=int(h0),
        params=np.array([f, cx, cy, k1], dtype=np.float64),
        prior_focal_length=True,
    )
    shared_cam_id = db.add_camera(cam_spec)

    image_ids: dict[str, int] = {}
    for p in imgs:
        cam_id = shared_cam_id
        if not single_camera:
            w, h = Image.open(p).size
            f_i = float(default_focal_length_factor) * float(max(w, h))
            cam_id = db.add_camera(
                CameraSpec(
                    model=camera_model,
                    width=int(w),
                    height=int(h),
                    params=np.array([f_i, float(w) / 2.0, float(h) / 2.0, 0.0], dtype=np.float64),
                    prior_focal_length=True,
                )
            )
        image_ids[p.name] = db.add_image(p.name, cam_id)

    # Learned extractor/matcher.
    device = matching.device if torch.cuda.is_available() and matching.device.startswith("cuda") else "cpu"
    if matching.extractor == "superpoint":
        extractor = SuperPoint(max_num_keypoints=int(matching.max_num_keypoints)).eval().to(device)
        matcher = LightGlue(features="superpoint", filter_threshold=float(matching.filter_threshold)).eval().to(device)
    elif matching.extractor == "disk":
        extractor = DISK(max_num_keypoints=int(matching.max_num_keypoints)).eval().to(device)
        matcher = LightGlue(features="disk", filter_threshold=float(matching.filter_threshold)).eval().to(device)
    elif matching.extractor == "aliked":
        extractor = ALIKED(max_num_keypoints=int(matching.max_num_keypoints)).eval().to(device)
        matcher = LightGlue(features="aliked", filter_threshold=float(matching.filter_threshold)).eval().to(device)
    elif matching.extractor == "sift":
        extractor = SIFT(max_num_keypoints=int(matching.max_num_keypoints)).eval().to(device)
        matcher = LightGlue(features="sift", filter_threshold=float(matching.filter_threshold)).eval().to(device)
    else:  # pragma: no cover
        raise ValueError(f"Unsupported extractor: {matching.extractor}")

    # Extract features once per image.
    feats_cache: dict[str, dict[str, object]] = {}
    for p in imgs:
        image_t = load_image(p).to(device)
        feats = extractor.extract(image_t, resize=None)
        feats = rbd(feats)
        kps = np.asarray(feats["keypoints"].detach().cpu().numpy(), dtype=np.float32)
        kps = kps + 0.5  # COLMAP origin convention
        db.add_keypoints_xy(image_ids[p.name], kps)
        feats_cache[p.name] = feats

    # Match pairs.
    matched_pairs = 0
    total_matches = 0
    for name0, name1 in pairs:
        if name0 not in feats_cache or name1 not in feats_cache:
            continue
        feats0 = feats_cache[name0]
        feats1 = feats_cache[name1]
        out = matcher({"image0": feats0, "image1": feats1})
        out = rbd(out)
        matches = np.asarray(out["matches"].detach().cpu().numpy(), dtype=np.int32)
        db.add_matches(image_ids[name0], image_ids[name1], matches)
        matched_pairs += 1
        total_matches += int(matches.shape[0])

    db.commit()
    db.close()

    env = dict(os.environ)
    run(
        [
            "colmap",
            "mapper",
            "--database_path",
            str(db_path),
            "--image_path",
            str(images_dir),
            "--output_path",
            str(sparse_dir),
        ],
        cwd=colmap_dir,
        env=env,
        stdout_path=logs_dir / "mapper.stdout.log",
        stderr_path=logs_dir / "mapper.stderr.log",
    )

    candidates = sorted([p for p in sparse_dir.iterdir() if p.is_dir() and p.name.isdigit()])
    if not candidates:
        raise FileNotFoundError(f"No COLMAP models found under: {sparse_dir}")

    models: dict[str, ColmapModel] = {}
    sparse_txt_root = colmap_dir / "sparse_txt"
    sparse_txt_root.mkdir(parents=True, exist_ok=True)
    for c in candidates:
        out_txt = sparse_txt_root / c.name
        out_txt.mkdir(parents=True, exist_ok=True)
        run(
            [
                "colmap",
                "model_converter",
                "--input_path",
                str(c),
                "--output_path",
                str(out_txt),
                "--output_type",
                "TXT",
            ],
            cwd=colmap_dir,
            env=env,
            stdout_path=logs_dir / f"model_converter.{c.name}.stdout.log",
            stderr_path=logs_dir / f"model_converter.{c.name}.stderr.log",
        )
        models[c.name] = load_colmap_model_txt(out_txt)

    best_id = max(models.keys(), key=lambda k: len(models[k].images))
    best_model_dir = sparse_dir / best_id
    sparse_txt_dir = sparse_txt_root / best_id
    model = models[best_id]
    diagnostics = {
        "backend": "colmap+lightglue",
        "camera_model": camera_model,
        "default_focal_length_factor": default_focal_length_factor,
        "single_camera": single_camera,
        "extractor": matching.extractor,
        "max_num_keypoints": matching.max_num_keypoints,
        "filter_threshold": matching.filter_threshold,
        "matched_pairs": matched_pairs,
        "total_matches": total_matches,
        "colmap_dir": str(colmap_dir),
        "best_model_dir": str(best_model_dir),
        "registered_images": len(model.images),
        "points3d": len(model.points3d),
        "logs_dir": str(logs_dir),
        "n_models": len(models),
        "models": {mid: {"registered_images": len(m.images), "points3d": len(m.points3d)} for mid, m in models.items()},
    }
    return ColmapLearnedRunResult(
        model=model,
        sparse_model_dir=best_model_dir,
        sparse_model_txt_dir=sparse_txt_dir,
        diagnostics=diagnostics,
        models=models,
    )
