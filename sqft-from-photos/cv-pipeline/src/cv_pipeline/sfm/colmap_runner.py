from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cv_pipeline.sfm.colmap_model import ColmapModel, load_colmap_model_txt
from cv_pipeline.utils.subprocess import require_binary, run


@dataclass(frozen=True)
class ColmapRunResult:
    model: ColmapModel
    sparse_model_dir: Path
    sparse_model_txt_dir: Path
    diagnostics: dict[str, object]


def _select_best_model_dir(sparse_dir: Path) -> Path:
    """
    COLMAP mapper can output multiple models as numbered subdirs.
    Heuristic: choose the one with the most registered images (lines in images.txt / 2).
    """
    candidates = sorted([p for p in sparse_dir.iterdir() if p.is_dir() and p.name.isdigit()])
    if not candidates:
        raise FileNotFoundError(f"No COLMAP models found under: {sparse_dir}")

    best = candidates[0]
    best_count = -1
    for c in candidates:
        txt_dir = c / "txt_tmp"
        # model_converter output goes elsewhere; but mapper writes binary. Just count images.bin existence? too hard.
        # Use number of files as proxy if images.bin exists.
        images_bin = c / "images.bin"
        points_bin = c / "points3D.bin"
        score = 0
        if images_bin.exists():
            score += 1
        if points_bin.exists():
            score += 1
        if score > best_count:
            best = c
            best_count = score
    return best


def run_colmap_sfm(
    images_dir: Path,
    work_dir: Path,
    *,
    camera_model: str = "SIMPLE_RADIAL",
    default_focal_length_factor: float = 1.2,
    single_camera: bool = True,
) -> ColmapRunResult:
    require_binary("colmap")
    work_dir.mkdir(parents=True, exist_ok=True)

    colmap_dir = work_dir / "colmap"
    colmap_dir.mkdir(parents=True, exist_ok=True)
    db_path = colmap_dir / "database.db"
    sparse_dir = colmap_dir / "sparse"
    sparse_dir.mkdir(parents=True, exist_ok=True)

    logs_dir = colmap_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)

    run(
        [
            "colmap",
            "feature_extractor",
            "--database_path",
            str(db_path),
            "--image_path",
            str(images_dir),
            "--ImageReader.camera_model",
            camera_model,
            "--ImageReader.default_focal_length_factor",
            str(default_focal_length_factor),
            "--ImageReader.single_camera",
            "1" if single_camera else "0",
        ],
        cwd=colmap_dir,
        env=env,
        stdout_path=logs_dir / "feature_extractor.stdout.log",
        stderr_path=logs_dir / "feature_extractor.stderr.log",
    )

    run(
        [
            "colmap",
            "exhaustive_matcher",
            "--database_path",
            str(db_path),
        ],
        cwd=colmap_dir,
        env=env,
        stdout_path=logs_dir / "exhaustive_matcher.stdout.log",
        stderr_path=logs_dir / "exhaustive_matcher.stderr.log",
    )

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

    best_model_dir = _select_best_model_dir(sparse_dir)
    sparse_txt_dir = colmap_dir / "sparse_txt"
    sparse_txt_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            str(best_model_dir),
            "--output_path",
            str(sparse_txt_dir),
            "--output_type",
            "TXT",
        ],
        cwd=colmap_dir,
        env=env,
        stdout_path=logs_dir / "model_converter.stdout.log",
        stderr_path=logs_dir / "model_converter.stderr.log",
    )

    model = load_colmap_model_txt(sparse_txt_dir)
    diagnostics = {
        "camera_model": camera_model,
        "default_focal_length_factor": default_focal_length_factor,
        "single_camera": single_camera,
        "colmap_dir": str(colmap_dir),
        "best_model_dir": str(best_model_dir),
        "registered_images": len(model.images),
        "points3d": len(model.points3d),
        "logs_dir": str(logs_dir),
    }
    return ColmapRunResult(
        model=model,
        sparse_model_dir=best_model_dir,
        sparse_model_txt_dir=sparse_txt_dir,
        diagnostics=diagnostics,
    )

