from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.paths import VolumePaths
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


def _pair_id(image_id1: int, image_id2: int) -> int:
    """
    Pair ID used by COLMAP: min(id1,id2) * 2147483647 + max(id1,id2)
    """
    if image_id1 > image_id2:
        image_id1, image_id2 = image_id2, image_id1
    return int(image_id1) * 2147483647 + int(image_id2)


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

    # Create a COLMAP DB with the *current* COLMAP schema by running feature_extractor.
    # Then overwrite keypoints/matches with learned ones, run geometric verification, and map.
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

    imgs = sorted([p for p in images_dir.iterdir() if p.is_file()])
    if not imgs:
        raise FileNotFoundError(f"No images found in: {images_dir}")

    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        rows = [(int(iid), str(name)) for iid, name in cur.execute("SELECT image_id, name FROM images")]
        if not rows:
            raise RuntimeError("COLMAP feature_extractor created no images in the database.")
        # COLMAP may store image names as paths (e.g. resolving symlinks). We primarily address images by basename.
        image_ids: dict[str, int] = {name: iid for iid, name in rows}
        image_ids_by_base: dict[str, int] = {}
        for iid, name in rows:
            base = Path(name).name
            image_ids_by_base.setdefault(base, iid)
        # Clear SIFT keypoints/descriptors/matches; we'll insert learned ones.
        cur.execute("DELETE FROM keypoints")
        cur.execute("DELETE FROM descriptors")
        cur.execute("DELETE FROM matches")
        cur.execute("DELETE FROM two_view_geometries")
        con.commit()
    finally:
        con.close()

    # Learned extractor/matcher.
    if torch.cuda.is_available() and matching.device.startswith("cuda"):
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
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
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        for p in imgs:
            image_t = load_image(p).to(device)
            feats = extractor.extract(image_t, resize=None)
            feats_cache[p.name] = feats

            image_id = image_ids_by_base.get(p.name)
            if image_id is None:
                continue
            feats_nb = rbd(feats)
            kps = np.asarray(feats_nb["keypoints"].detach().cpu().numpy(), dtype=np.float32)
            kps = kps + 0.5  # COLMAP origin convention
            # COLMAP keypoint layout: (x, y, a11, a12, a21, a22). Pad with identity affine.
            pad = np.tile(np.asarray([1.0, 0.0, 0.0, 1.0], dtype=np.float32)[None, :], (kps.shape[0], 1))
            kps6 = np.concatenate([kps, pad], axis=1)
            cur.execute(
                "INSERT INTO keypoints(image_id,rows,cols,data) VALUES(?,?,?,?)",
                (int(image_id), int(kps6.shape[0]), int(kps6.shape[1]), kps6.tobytes()),
            )
        con.commit()
    finally:
        con.close()

    # Match pairs.
    matched_pairs = 0
    total_matches = 0
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        for name0, name1 in pairs:
            if name0 not in feats_cache or name1 not in feats_cache:
                continue
            id0 = image_ids_by_base.get(name0)
            id1 = image_ids_by_base.get(name1)
            if id0 is None or id1 is None:
                continue
            feats0 = feats_cache[name0]
            feats1 = feats_cache[name1]
            out = matcher({"image0": feats0, "image1": feats1})
            out_nb = rbd(out)
            matches = np.asarray(out_nb["matches"].detach().cpu().numpy(), dtype=np.int32)
            if matches.ndim != 2 or matches.shape[1] != 2:
                continue
            pid = _pair_id(int(id0), int(id1))
            cur.execute(
                "INSERT OR REPLACE INTO matches(pair_id,rows,cols,data) VALUES(?,?,?,?)",
                (int(pid), int(matches.shape[0]), int(matches.shape[1]), matches.tobytes()),
            )
            matched_pairs += 1
            total_matches += int(matches.shape[0])
        con.commit()
    finally:
        con.close()

    run(
        [
            "colmap",
            "geometric_verifier",
            "--database_path",
            str(db_path),
        ],
        cwd=colmap_dir,
        env=env,
        stdout_path=logs_dir / "geometric_verifier.stdout.log",
        stderr_path=logs_dir / "geometric_verifier.stderr.log",
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
