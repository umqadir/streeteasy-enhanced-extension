from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from cv_pipeline.dataset import load_streeteasy_dataset
from cv_pipeline.depth import DepthAnythingV2Metric, DepthConfig
from cv_pipeline.geometry.footprint import estimate_floor_footprint_sqft
from cv_pipeline.geometry.planes import estimate_gravity_up, find_floor_plane
from cv_pipeline.image import preprocess_images
from cv_pipeline.paths import VolumePaths, WorkPaths, default_volume_root, default_work_root, ensure_dirs, setup_model_caches
from cv_pipeline.pipeline.types import RunArtifacts
from cv_pipeline.sfm import run_colmap_sfm
from cv_pipeline.sfm.scale import estimate_scale_from_depth_alignment
from cv_pipeline.utils.ids import new_run_id


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_point_cloud(
    *,
    model,
    depth_dir: Path,
    scale_m_per_sfm: float,
    pc_stride: int,
    max_depth_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns (points_world_m, camera_centers_m, gravity_up).
    """
    points_all: list[np.ndarray] = []
    camera_centers: list[np.ndarray] = []
    rotations: list[np.ndarray] = []

    for img in model.images.values():
        cam = model.cameras[img.camera_id]
        fx, fy, cx, cy = cam.intrinsics_pinhole()
        depth_path = depth_dir / f"{Path(img.name).stem}_raw_depth_meter.npy"
        if not depth_path.exists():
            continue
        depth = np.load(depth_path).astype(np.float64)

        h, w = depth.shape[:2]
        ys = np.arange(0, h, pc_stride, dtype=np.int32)
        xs = np.arange(0, w, pc_stride, dtype=np.int32)
        gx, gy = np.meshgrid(xs, ys)
        z = depth[gy, gx]
        mask = np.isfinite(z) & (z > 0.05) & (z < max_depth_m)
        if not mask.any():
            continue

        gx = gx[mask].astype(np.float64)
        gy = gy[mask].astype(np.float64)
        z = z[mask].astype(np.float64)

        x = (gx - cx) / fx * z
        y = (gy - cy) / fy * z
        pts_cam = np.stack([x, y, z], axis=1)  # meters

        r = img.rotation_matrix().astype(np.float64)
        t = img.tvec.astype(np.float64) * float(scale_m_per_sfm)  # meters

        # world = R^T * (cam - t) for column vectors; vectorized:
        pts_world = (r.T @ (pts_cam.T - t[:, None])).T
        points_all.append(pts_world)

        camera_centers.append(img.camera_center().astype(np.float64) * float(scale_m_per_sfm))
        rotations.append(r)

    if not points_all:
        raise RuntimeError("No depth maps found to build a point cloud")

    pts = np.concatenate(points_all, axis=0)
    cams = np.stack(camera_centers, axis=0) if camera_centers else np.zeros((0, 3), dtype=np.float64)
    up = estimate_gravity_up(rotations)
    return pts, cams, up


def _point_cloud_from_depth(
    depth_m: np.ndarray,
    *,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    pc_stride: int,
    max_depth_m: float,
) -> np.ndarray:
    h, w = depth_m.shape[:2]
    ys = np.arange(0, h, pc_stride, dtype=np.int32)
    xs = np.arange(0, w, pc_stride, dtype=np.int32)
    gx, gy = np.meshgrid(xs, ys)
    z = depth_m[gy, gx]
    mask = np.isfinite(z) & (z > 0.05) & (z < max_depth_m)
    if not mask.any():
        return np.zeros((0, 3), dtype=np.float64)

    gx = gx[mask].astype(np.float64)
    gy = gy[mask].astype(np.float64)
    z = z[mask].astype(np.float64)

    x = (gx - cx) / fx * z
    y = (gy - cy) / fy * z
    return np.stack([x, y, z], axis=1)


def _single_view_area_sqft(
    *,
    depth_m: np.ndarray,
    pc_stride: int,
    max_depth_m: float,
    alpha: float,
    assumed_fov_deg: float = 70.0,
) -> tuple[float, str, dict[str, object]]:
    """
    Depth-only fallback: estimate *visible* floor patch area from a single image.
    This is not a full-apartment estimate; the caller should apply a conservative expansion prior.
    """
    h, w = depth_m.shape[:2]
    f = 0.5 * float(w) / float(np.tan(np.deg2rad(assumed_fov_deg) / 2.0))
    fx = f
    fy = f
    cx = 0.5 * float(w)
    cy = 0.5 * float(h)

    pts = _point_cloud_from_depth(
        depth_m,
        fx=fx,
        fy=fy,
        cx=cx,
        cy=cy,
        pc_stride=pc_stride,
        max_depth_m=max_depth_m,
    )
    if pts.shape[0] == 0:
        raise RuntimeError("No valid depth points for single-view point cloud")

    rng = np.random.default_rng(0)
    gravity_up_cam = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    plane, inliers, plane_diag = find_floor_plane(
        pts,
        camera_centers=np.zeros((1, 3), dtype=np.float64),
        gravity_up=gravity_up_cam,
        distance_thresh=0.06,
        rng=rng,
    )
    footprint = estimate_floor_footprint_sqft(pts, plane, inliers, alpha=alpha)

    diag = {
        "assumed_fov_deg": assumed_fov_deg,
        "intrinsics": {"fx": fx, "fy": fy, "cx": cx, "cy": cy},
        "pointcloud_points": int(pts.shape[0]),
        "floor_plane": {**plane_diag, "normal": plane.normal.tolist(), "d": float(plane.d)},
        "footprint": {**footprint.diagnostics, "area_m2": footprint.area_m2, "area_sqft": footprint.area_sqft},
    }
    return float(footprint.area_sqft), footprint.polygon_wkt, diag


def _estimate_interval_90(sqft: float, scale_rel_std: float, coverage_penalty: float) -> tuple[float, float]:
    """
    v0 heuristic: treat scale uncertainty as log-normal on area.
    """
    # area scales as s^2
    sigma = max(1e-6, 2.0 * float(scale_rel_std))
    sigma = sigma * (1.0 + float(coverage_penalty))
    z = 1.645  # 90% interval
    lo = sqft * float(np.exp(-z * sigma))
    hi = sqft * float(np.exp(z * sigma))
    return lo, hi


def run_listing(
    *,
    images_dir: Path,
    listing_id: str | None,
    label_sqft: float | None,
    max_side: int,
    use_colmap: bool,
    depth_model: str,
    depth_encoder: str,
    depth_dataset: str,
    depth_input_size: int,
    max_depth_m: float,
    pc_stride: int,
    alpha: float,
    out_json: Path | None,
) -> dict[str, object]:
    run_id = new_run_id(prefix=listing_id or "listing")
    volume = VolumePaths(root=default_volume_root())
    work = WorkPaths(root=default_work_root())
    ensure_dirs(volume.runs_dir, work.root, volume.models_dir, volume.checkpoints_dir, volume.vendor_dir)
    setup_model_caches(volume)

    work_dir = work.root / run_id
    volume_dir = volume.runs_dir / run_id
    ensure_dirs(work_dir, volume_dir)

    artifacts = RunArtifacts(
        run_id=run_id,
        work_dir=work_dir,
        volume_dir=volume_dir,
        images_dir=images_dir,
        preprocessed_dir=work_dir / "images_preprocessed",
        colmap_dir=work_dir / "colmap_run",
        depth_dir=work_dir / "depth",
    )
    ensure_dirs(artifacts.preprocessed_dir, artifacts.colmap_dir, artifacts.depth_dir)

    pre = preprocess_images(images_dir, artifacts.preprocessed_dir, max_side=max_side)

    diagnostics: dict[str, object] = {"preprocess": pre.diagnostics}
    run_config: dict[str, object] = {
        "images_dir": str(images_dir),
        "listing_id": listing_id,
        "label_sqft": label_sqft,
        "max_side": max_side,
        "use_colmap": use_colmap,
        "depth_model": depth_model,
        "depth_encoder": depth_encoder,
        "depth_dataset": depth_dataset,
        "depth_input_size": depth_input_size,
        "max_depth_m": max_depth_m,
        "pc_stride": pc_stride,
        "alpha": alpha,
    }
    model = None
    colmap_error: str | None = None
    if use_colmap:
        try:
            colmap = run_colmap_sfm(artifacts.preprocessed_dir, artifacts.colmap_dir)
            model = colmap.model
            diagnostics["colmap"] = colmap.diagnostics
        except Exception as e:
            colmap_error = str(e)
            diagnostics["colmap_error"] = colmap_error

    if depth_model != "depth-anything-metric":
        raise ValueError(f"Unsupported depth model: {depth_model}")

    depth_cfg = DepthConfig(
        encoder=depth_encoder,
        dataset=depth_dataset,
        input_size=depth_input_size,
        max_depth_m=max_depth_m,
    )
    depth_est = DepthAnythingV2Metric(volume=volume, cfg=depth_cfg)

    footprint_wkt = ""
    if model is not None:
        diagnostics["path"] = "colmap+metric-depth"
        # Infer depth for each registered image.
        depth_paths: list[str] = []
        for img in model.images.values():
            img_path = artifacts.preprocessed_dir / Path(img.name).name
            if not img_path.exists():
                # COLMAP stores name relative to image_path; we saved with basename.
                img_path = artifacts.preprocessed_dir / img.name
            out_path = artifacts.depth_dir / f"{Path(img.name).stem}_raw_depth_meter.npy"
            depth_est.infer_to_npy(img_path, out_path)
            depth_paths.append(str(out_path))
        diagnostics["depth"] = {
            "model": depth_model,
            "encoder": depth_encoder,
            "dataset": depth_dataset,
            "input_size": depth_input_size,
            "max_depth_m": max_depth_m,
            "depth_maps": len(depth_paths),
        }

        scale = estimate_scale_from_depth_alignment(model, artifacts.depth_dir, max_depth_m=max_depth_m)
        diagnostics["scale"] = {
            "scale_m_per_sfm": scale.scale_m_per_sfm,
            "scale_rel_std": scale.scale_rel_std,
            "pairs_inliers": scale.inliers,
            "pairs_total": scale.total,
            "images_used": len(scale.per_image_scales),
        }

        points_world_m, camera_centers_m, up = _build_point_cloud(
            model=model,
            depth_dir=artifacts.depth_dir,
            scale_m_per_sfm=scale.scale_m_per_sfm,
            pc_stride=pc_stride,
            max_depth_m=max_depth_m,
        )
        diagnostics["pointcloud"] = {
            "points": int(points_world_m.shape[0]),
            "pc_stride": pc_stride,
            "cameras": int(camera_centers_m.shape[0]),
            "gravity_up": up.tolist(),
        }

        rng = np.random.default_rng(0)
        plane, inliers, plane_diag = find_floor_plane(
            points_world_m,
            camera_centers=camera_centers_m,
            gravity_up=up,
            distance_thresh=0.06,
            rng=rng,
        )
        diagnostics["floor_plane"] = {**plane_diag, "normal": plane.normal.tolist(), "d": float(plane.d)}

        footprint = estimate_floor_footprint_sqft(points_world_m, plane, inliers, alpha=alpha)
        diagnostics["footprint"] = {
            **footprint.diagnostics,
            "area_m2": footprint.area_m2,
            "area_sqft": footprint.area_sqft,
        }
        footprint_wkt = footprint.polygon_wkt

        sqft = float(footprint.area_sqft)

        coverage_penalty = float(max(0.0, 0.15 - float(plane_diag["inlier_ratio"])) / 0.15)
        lo, hi = _estimate_interval_90(sqft, scale_rel_std=scale.scale_rel_std, coverage_penalty=coverage_penalty)

        conf = float(
            max(0.0, 1.0 - 4.0 * scale.scale_rel_std) * min(1.0, float(plane_diag["inlier_ratio"]) / 0.25)
        )
        area_m2 = float(footprint.area_m2)
    else:
        diagnostics["path"] = "depth-only"
        if colmap_error:
            diagnostics["fallback_reason"] = colmap_error

        # Infer depth for each preprocessed image and estimate visible floor patch area per image.
        candidates: list[tuple[float, str, dict[str, object], str]] = []
        for img_path in pre.kept_images:
            out_path = artifacts.depth_dir / f"{img_path.stem}_raw_depth_meter.npy"
            depth_est.infer_to_npy(img_path, out_path)
            depth_m = np.load(out_path)
            try:
                area_sqft, poly_wkt, diag = _single_view_area_sqft(
                    depth_m=depth_m,
                    pc_stride=pc_stride,
                    max_depth_m=max_depth_m,
                    alpha=alpha,
                )
                candidates.append((area_sqft, poly_wkt, diag, img_path.name))
            except Exception as e:
                candidates.append((0.0, "", {"error": str(e)}, img_path.name))

        best = max(candidates, key=lambda t: t[0]) if candidates else (0.0, "", {}, "")
        visible_sqft = float(best[0])
        footprint_wkt = best[1]
        diagnostics["depth_only"] = {
            "best_image": best[3],
            "visible_floor_sqft": visible_sqft,
            "per_image": [{"image": name, "visible_floor_sqft": a, "diag": d} for a, _, d, name in candidates],
        }

        # Expansion prior to map visible patch → apartment sqft (very rough; tune/calibrate later).
        expansion = 3.5
        sqft = visible_sqft * expansion
        lo = max(0.0, visible_sqft)  # at least what was observed
        hi = max(lo, visible_sqft * 8.0)
        conf = 0.05
        area_m2 = None

    result: dict[str, object] = {
        "run_id": run_id,
        "listing_id": listing_id or images_dir.name,
        "label_sqft": label_sqft,
        "sqft_estimate": sqft,
        "sqft_interval_90": [lo, hi],
        "confidence_score": conf,
        "area_m2": area_m2,
        "diagnostics": diagnostics,
        "artifacts": {
            "volume_dir": str(volume_dir),
            "work_dir": str(work_dir),
            "depth_dir": str(artifacts.depth_dir),
            "colmap_dir": str(artifacts.colmap_dir),
            "images_preprocessed": str(artifacts.preprocessed_dir),
        },
    }

    _write_json(volume_dir / "run_config.json", run_config)
    if footprint_wkt:
        _write_text(volume_dir / "footprint.wkt", footprint_wkt)

    out_path = out_json or (volume_dir / "result.json")
    _write_json(out_path, result)
    _write_json(volume_dir / "diagnostics.json", diagnostics)
    return result


def run_streeteasy_eval(
    *,
    dataset_path: Path,
    downloads_dir: Path,
    limit: int,
    out_json: Path | None,
) -> dict[str, object]:
    examples = load_streeteasy_dataset(dataset_path, downloads_dir)
    if limit and limit > 0:
        examples = examples[:limit]

    rows: list[dict[str, object]] = []
    for ex in examples:
        if not ex.images_dir.exists():
            rows.append(
                {
                    "listing_id": ex.listing_id,
                    "listing_url": ex.listing_url,
                    "label_sqft": ex.sqft,
                    "error": f"missing images_dir: {ex.images_dir}",
                }
            )
            continue
        try:
            res = run_listing(
                images_dir=ex.images_dir,
                listing_id=ex.listing_id,
                label_sqft=ex.sqft,
                max_side=1600,
                use_colmap=True,
                depth_model="depth-anything-metric",
                depth_encoder="vitl",
                depth_dataset="hypersim",
                depth_input_size=518,
                max_depth_m=20.0,
                pc_stride=4,
                alpha=0.0,
                out_json=None,
            )
            rows.append(
                {
                    "listing_id": ex.listing_id,
                    "listing_url": ex.listing_url,
                    "label_sqft": ex.sqft,
                    "pred_sqft": res["sqft_estimate"],
                    "interval_90": res["sqft_interval_90"],
                    "confidence": res["confidence_score"],
                    "run_id": res["run_id"],
                }
            )
        except Exception as e:  # pragma: no cover
            rows.append(
                {
                    "listing_id": ex.listing_id,
                    "listing_url": ex.listing_url,
                    "label_sqft": ex.sqft,
                    "error": str(e),
                }
            )

    labeled = [r for r in rows if isinstance(r.get("label_sqft"), (int, float)) and isinstance(r.get("pred_sqft"), (int, float))]
    if labeled:
        y = np.array([float(r["label_sqft"]) for r in labeled], dtype=np.float64)
        yhat = np.array([float(r["pred_sqft"]) for r in labeled], dtype=np.float64)
        mae = float(np.mean(np.abs(yhat - y)))
        mape = float(np.mean(np.abs(yhat - y) / np.maximum(y, 1e-9)))
    else:
        mae = None
        mape = None

    summary: dict[str, object] = {
        "dataset": str(dataset_path),
        "downloads": str(downloads_dir),
        "n": len(rows),
        "n_labeled": len(labeled),
        "mae_sqft": mae,
        "mape": mape,
        "rows": rows,
    }
    out_path = out_json or (VolumePaths(root=default_volume_root()).runs_dir / new_run_id(prefix="eval") / "eval.json")
    _write_json(out_path, summary)
    return summary
