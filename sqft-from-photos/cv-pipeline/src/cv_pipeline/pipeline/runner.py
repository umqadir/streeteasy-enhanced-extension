from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.dataset import load_streeteasy_dataset
from cv_pipeline.depth import (
    DepthAnythingV2Metric,
    DepthConfig,
    Metric3DConfig,
    Metric3DV2,
    MoGeV2Config,
    MoGeV2Metric,
    UniDepthV1Config,
    UniDepthV1Metric,
    ZoeDepthConfig,
    ZoeDepthMetric,
)
from cv_pipeline.depth.base import PinholeIntrinsics
from cv_pipeline.geometry.footprint import estimate_floor_footprint_sqft
from cv_pipeline.geometry.planes import estimate_gravity_up, find_floor_plane
from cv_pipeline.fusion import tsdf_fuse_open3d
from cv_pipeline.image import preprocess_images
from cv_pipeline.paths import VolumePaths, WorkPaths, default_volume_root, default_work_root, ensure_dirs, setup_model_caches
from cv_pipeline.pipeline.types import RunArtifacts
from cv_pipeline.retrieval import EmbeddingBackend, build_topk_pairs, compute_image_embeddings, connected_components_from_pairs
from cv_pipeline.sfm import LearnedMatchingConfig, run_colmap_sfm, run_colmap_sfm_lightglue
from cv_pipeline.sfm.scale import estimate_scale_from_depth_alignment
from cv_pipeline.sfm.scale_depthmaps import estimate_scale_from_depthmaps
from cv_pipeline.reconstruction import Dust3RConfig, MASt3RConfig, run_dust3r_reconstruction, run_mast3r_reconstruction
from cv_pipeline.uncertainty import MonteCarloConfig, monte_carlo_sqft
from cv_pipeline.utils.ids import new_run_id


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _slug(s: str) -> str:
    keep = []
    for ch in s.lower():
        if ch.isalnum() or ch in {"-", "_"}:
            keep.append(ch)
        elif ch in {" ", "/", ":"}:
            keep.append("_")
    out = "".join(keep).strip("_")
    return out or "x"


@dataclass(frozen=True)
class GeometryEstimate:
    sqft: float
    interval_90: tuple[float, float]
    confidence: float
    area_m2: float | None
    footprint_wkt: str
    diagnostics: dict[str, object]
    samples_path: str | None = None


def _area_sqft_from_polygon_wkt(wkt: str) -> float | None:
    if not wkt:
        return None
    try:
        from shapely import wkt as shapely_wkt
    except Exception:  # pragma: no cover
        return None
    try:
        geom = shapely_wkt.loads(wkt)
        if geom is None or geom.is_empty:
            return None
        area_m2 = float(geom.area)
        return area_m2 * 10.763910416709722
    except Exception:
        return None


def _layout_prior_room_area_sqft(
    visible_poly_wkt: str,
    *,
    expand: float = 1.15,
    max_multiplier: float = 6.0,
) -> tuple[float | None, dict[str, object]]:
    """
    Heuristic "layout prior" for depth-only fallback.

    Converts the visible floor patch polygon into an oriented bounding rectangle and expands it
    slightly to approximate a full-room footprint.
    """
    diag: dict[str, object] = {"expand": float(expand), "max_multiplier": float(max_multiplier)}
    if not visible_poly_wkt:
        return None, {**diag, "reason": "no_polygon"}

    try:
        from shapely import affinity, wkt as shapely_wkt
    except Exception as e:  # pragma: no cover
        return None, {**diag, "error": f"missing shapely: {e}"}

    try:
        geom = shapely_wkt.loads(visible_poly_wkt)
        if geom is None or geom.is_empty:
            return None, {**diag, "reason": "empty_geom"}
        if geom.geom_type == "MultiPolygon":
            geom = max(list(geom.geoms), key=lambda g: g.area)
        visible_m2 = float(getattr(geom, "area", 0.0))
        if not np.isfinite(visible_m2) or visible_m2 <= 1e-9:
            return None, {**diag, "reason": "bad_visible_area", "visible_m2": visible_m2}

        rect = geom.minimum_rotated_rectangle
        rect_m2 = float(getattr(rect, "area", 0.0))
        if not np.isfinite(rect_m2) or rect_m2 <= 1e-9:
            return None, {**diag, "reason": "bad_rect_area", "rect_m2": rect_m2}

        if float(expand) != 1.0:
            rect = affinity.scale(rect, xfact=float(expand), yfact=float(expand), origin="center")
        room_m2 = float(getattr(rect, "area", 0.0))
        if not np.isfinite(room_m2) or room_m2 <= 1e-9:
            return None, {**diag, "reason": "bad_room_area", "room_m2": room_m2}

        # Clamp pathological extrapolations.
        mult = room_m2 / visible_m2
        if mult > float(max_multiplier):
            room_m2 = visible_m2 * float(max_multiplier)
            mult = float(max_multiplier)

        return room_m2 * 10.763910416709722, {
            **diag,
            "visible_m2": visible_m2,
            "rect_m2": rect_m2,
            "room_m2": room_m2,
            "multiplier": float(mult),
        }
    except Exception as e:
        return None, {**diag, "error": str(e)}

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
    intrinsics: PinholeIntrinsics | None,
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
    if intrinsics is not None:
        fx, fy, cx, cy = float(intrinsics.fx), float(intrinsics.fy), float(intrinsics.cx), float(intrinsics.cy)
        assumed_fov_deg = float(
            2.0 * np.rad2deg(np.arctan(0.5 * float(intrinsics.width) / max(1e-9, float(intrinsics.fx))))
        )
    else:
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


def _make_depth_estimators(
    *,
    volume: VolumePaths,
    depth_model: str,
    depth_encoder: str,
    depth_dataset: str,
    depth_input_size: int,
    max_depth_m: float,
    depth_ensemble: list[str] | None,
) -> dict[str, object]:
    """
    Returns:
      {"estimators": {key: estimator}, "diagnostics": {...}}

    Keys are stable directory-friendly slugs used under artifacts.depth_dir.
    """
    if depth_model != "ensemble":
        depth_ensemble = [depth_model]
    if not depth_ensemble:
        raise ValueError("depth_ensemble must be non-empty")

    estimators: dict[str, object] = {}
    used: list[dict[str, object]] = []

    for name in depth_ensemble:
        if name == "depth-anything-metric":
            depth_cfg = DepthConfig(
                encoder=depth_encoder,
                dataset=depth_dataset,
                input_size=depth_input_size,
                max_depth_m=max_depth_m,
            )
            est = DepthAnythingV2Metric(volume=volume, cfg=depth_cfg)
            key = _slug(f"depth-anything-metric-{depth_dataset}-{depth_encoder}")
            used.append({"name": name, "key": key, "encoder": depth_encoder, "dataset": depth_dataset})
        elif name == "unidepth-v1":
            cfg = UniDepthV1Config()
            est = UniDepthV1Metric(volume=volume, cfg=cfg)
            key = _slug(f"unidepth-v1-{cfg.repo}")
            used.append({"name": name, "key": key, "repo": cfg.repo})
        elif name == "moge-v2":
            cfg = MoGeV2Config()
            est = MoGeV2Metric(volume=volume, cfg=cfg)
            key = _slug(f"moge-v2-{cfg.repo}")
            used.append({"name": name, "key": key, "repo": cfg.repo})
        elif name == "metric3d-v2":
            cfg = Metric3DConfig(model="vit_small", max_depth_m=max_depth_m)
            est = Metric3DV2(volume=volume, cfg=cfg)
            key = _slug(f"metric3d-v2-{cfg.model}")
            used.append({"name": name, "key": key, "model": cfg.model})
        elif name == "zoedepth":
            cfg = ZoeDepthConfig()
            est = ZoeDepthMetric(cfg=cfg)
            key = _slug(f"zoedepth-{cfg.hub_model}")
            used.append({"name": name, "key": key, "hub_model": cfg.hub_model})
        else:
            raise ValueError(f"Unsupported depth model: {name}")

        if key in estimators:  # pragma: no cover
            raise ValueError(f"Duplicate depth estimator key: {key}")
        estimators[key] = est

    return {"estimators": estimators, "diagnostics": {"depth_model": depth_model, "depth_ensemble": used}}


def _infer_depth_maps_for_colmap_model(
    *,
    model,
    depth_est,
    artifacts: RunArtifacts,
    depth_dir: Path,
    max_depth_m: float,
) -> dict[str, object]:
    ensure_dirs(depth_dir)
    depth_paths: list[str] = []
    for img in model.images.values():
        img_path = artifacts.preprocessed_dir / Path(img.name).name
        if not img_path.exists():
            img_path = artifacts.preprocessed_dir / img.name
        out_path = depth_dir / f"{Path(img.name).stem}_raw_depth_meter.npy"
        cam = model.cameras[img.camera_id]
        fx, fy, cx, cy = cam.intrinsics_pinhole()
        intr = PinholeIntrinsics(fx=fx, fy=fy, cx=cx, cy=cy, width=int(cam.width), height=int(cam.height))
        depth_est.infer_to_npy(img_path, out_path, intrinsics=intr)
        depth_paths.append(str(out_path))
    return {"depth_maps": int(len(depth_paths)), "max_depth_m": float(max_depth_m), "depth_dir": str(depth_dir)}


def _run_colmap_geometry(
    *,
    component_id: str,
    model,
    depth_key: str,
    depth_est,
    artifacts: RunArtifacts,
    volume_dir: Path,
    sfm_images_dir: Path,
    max_depth_m: float,
    pc_stride: int,
    alpha: float,
    fusion: str,
    uncertainty: str,
    mc_samples: int,
) -> GeometryEstimate:
    """
    Run the COLMAP+metric-depth geometry path on a single COLMAP sub-model.
    """
    depth_dir = artifacts.depth_dir / depth_key
    depth_diag = _infer_depth_maps_for_colmap_model(
        model=model,
        depth_est=depth_est,
        artifacts=artifacts,
        depth_dir=depth_dir,
        max_depth_m=max_depth_m,
    )

    scale = estimate_scale_from_depth_alignment(model, depth_dir, max_depth_m=max_depth_m)
    scale_diag = {
        "scale_m_per_sfm": scale.scale_m_per_sfm,
        "scale_rel_std": scale.scale_rel_std,
        "pairs_inliers": scale.inliers,
        "pairs_total": scale.total,
        "images_used": len(scale.per_image_scales),
    }

    rotations = [im.rotation_matrix().astype(np.float64) for im in model.images.values()]
    up = estimate_gravity_up(rotations)
    camera_centers_m = np.stack(
        [im.camera_center().astype(np.float64) * float(scale.scale_m_per_sfm) for im in model.images.values()],
        axis=0,
    )

    if fusion == "tsdf":
        points_world_m, tsdf_diag = tsdf_fuse_open3d(
            model=model,
            images_dir=sfm_images_dir,
            depth_dir=depth_dir,
            scale_m_per_sfm=scale.scale_m_per_sfm,
            voxel_length=0.03,
            sdf_trunc=0.10,
            max_depth_m=max_depth_m,
        )
        fusion_diag = {"type": "tsdf-open3d", **tsdf_diag}
    else:
        points_world_m, camera_centers_m2, up2 = _build_point_cloud(
            model=model,
            depth_dir=depth_dir,
            scale_m_per_sfm=scale.scale_m_per_sfm,
            pc_stride=pc_stride,
            max_depth_m=max_depth_m,
        )
        camera_centers_m = camera_centers_m2
        up = up2
        fusion_diag = {"type": "none"}

    rng = np.random.default_rng(0)
    plane, inliers, plane_diag = find_floor_plane(
        points_world_m,
        camera_centers=camera_centers_m,
        gravity_up=up,
        distance_thresh=0.06,
        rng=rng,
    )
    footprint = estimate_floor_footprint_sqft(points_world_m, plane, inliers, alpha=alpha)

    # Write component footprint for debugging (note: plane-local coordinates).
    fp_path = volume_dir / f"footprint_{_slug(depth_key)}_comp{_slug(component_id)}.wkt"
    if footprint.polygon_wkt:
        _write_text(fp_path, footprint.polygon_wkt)

    samples_path: str | None = None
    if uncertainty == "montecarlo":
        rng = np.random.default_rng(0)
        samples, mc_diag = monte_carlo_sqft(
            points_world_m=points_world_m,
            camera_centers_m=camera_centers_m,
            gravity_up=up,
            scale_rel_std=scale.scale_rel_std,
            alpha=alpha,
            rng=rng,
            cfg=MonteCarloConfig(n=int(mc_samples), scale_rel_std=float(scale.scale_rel_std), alpha=float(alpha)),
        )
        if samples.size > 0:
            samples_file = volume_dir / f"samples_{_slug(depth_key)}_comp{_slug(component_id)}.npy"
            np.save(samples_file, samples.astype(np.float32))
            samples_path = str(samples_file)
            sqft = float(np.median(samples))
            lo = float(np.percentile(samples, 5))
            hi = float(np.percentile(samples, 95))
        else:
            sqft = float(footprint.area_sqft)
            coverage_penalty = float(max(0.0, 0.15 - float(plane_diag["inlier_ratio"])) / 0.15)
            lo, hi = _estimate_interval_90(sqft, scale_rel_std=scale.scale_rel_std, coverage_penalty=coverage_penalty)
        unc_diag = {**mc_diag, "p05": lo, "p95": hi, "median": sqft}
    else:
        sqft = float(footprint.area_sqft)
        coverage_penalty = float(max(0.0, 0.15 - float(plane_diag["inlier_ratio"])) / 0.15)
        lo, hi = _estimate_interval_90(sqft, scale_rel_std=scale.scale_rel_std, coverage_penalty=coverage_penalty)
        unc_diag = {"type": "heuristic", "p05": lo, "p95": hi}

    width = float(max(hi - lo, 0.0))
    rel_width = width / max(1.0, float(sqft))
    conf = float(
        max(0.0, 1.0 - 3.0 * rel_width)
        * max(0.0, 1.0 - 4.0 * scale.scale_rel_std)
        * min(1.0, float(plane_diag["inlier_ratio"]) / 0.25)
    )

    diag = {
        "component_id": component_id,
        "depth_key": depth_key,
        "depth": depth_diag,
        "scale": scale_diag,
        "fusion": fusion_diag,
        "pointcloud": {
            "points": int(points_world_m.shape[0]),
            "pc_stride": int(pc_stride),
            "cameras": int(camera_centers_m.shape[0]),
            "gravity_up": up.tolist(),
        },
        "floor_plane": {**plane_diag, "normal": plane.normal.tolist(), "d": float(plane.d)},
        "footprint": {
            **footprint.diagnostics,
            "area_m2": footprint.area_m2,
            "area_sqft": footprint.area_sqft,
            "footprint_path": str(fp_path) if fp_path.exists() else None,
        },
        "uncertainty": unc_diag,
    }

    return GeometryEstimate(
        sqft=float(sqft),
        interval_90=(float(lo), float(hi)),
        confidence=float(conf),
        area_m2=float(footprint.area_m2),
        footprint_wkt=footprint.polygon_wkt,
        diagnostics=diag,
        samples_path=samples_path,
    )

def run_listing(
    *,
    images_dir: Path,
    listing_id: str | None,
    label_sqft: float | None,
    max_side: int,
    use_colmap: bool,
    sfm_matching: str = "exhaustive",  # exhaustive|lightglue
    pair_embed: str = "torchvision-resnet50",
    pair_topk: int = 10,
    pair_min_sim: float = 0.2,
    multi_component: str = "best",  # best|sum
    depth_model: str,
    depth_encoder: str,
    depth_dataset: str,
    depth_input_size: int,
    depth_ensemble: list[str] | None = None,
    max_depth_m: float,
    pc_stride: int,
    alpha: float,
    fusion: str = "none",  # none|tsdf
    uncertainty: str = "heuristic",  # heuristic|montecarlo
    mc_samples: int = 200,
    fallback: str = "depth-only",  # depth-only|dust3r|mast3r
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
    # Run SfM on the deduplicated subset to avoid near-duplicate failure modes.
    sfm_images_dir = work_dir / "images_sfm"
    ensure_dirs(sfm_images_dir)
    for src in pre.kept_images:
        dst = sfm_images_dir / src.name
        if dst.exists():
            continue
        try:
            dst.symlink_to(src)
        except Exception:
            import shutil

            shutil.copy2(src, dst)

    diagnostics: dict[str, object] = {"preprocess": pre.diagnostics}
    run_config: dict[str, object] = {
        "images_dir": str(images_dir),
        "listing_id": listing_id,
        "label_sqft": label_sqft,
        "max_side": max_side,
        "use_colmap": use_colmap,
        "sfm_matching": sfm_matching,
        "pair_embed": pair_embed,
        "pair_topk": pair_topk,
        "pair_min_sim": pair_min_sim,
        "multi_component": multi_component,
        "depth_model": depth_model,
        "depth_encoder": depth_encoder,
        "depth_dataset": depth_dataset,
        "depth_input_size": depth_input_size,
        "depth_ensemble": depth_ensemble,
        "max_depth_m": max_depth_m,
        "pc_stride": pc_stride,
        "alpha": alpha,
        "fusion": fusion,
        "uncertainty": uncertainty,
        "mc_samples": mc_samples,
        "fallback": fallback,
    }
    model = None
    colmap = None
    colmap_error: str | None = None
    if use_colmap:
        try:
            if sfm_matching == "exhaustive":
                colmap = run_colmap_sfm(sfm_images_dir, artifacts.colmap_dir)
                model = colmap.model
                diagnostics["sfm"] = colmap.diagnostics
            elif sfm_matching == "lightglue":
                backend = EmbeddingBackend(pair_embed)
                emb = compute_image_embeddings(pre.kept_images, backend=backend, device="cuda", batch_size=8)
                sel = build_topk_pairs(emb.embeddings, k=pair_topk, min_cosine_sim=pair_min_sim, mutual=True)
                pairs = [(emb.image_names[i], emb.image_names[j]) for i, j in sel.pairs]
                diagnostics["retrieval_pairs"] = {
                    "embeddings": emb.diagnostics,
                    "pair_selection": sel.diagnostics,
                }
                colmap = run_colmap_sfm_lightglue(
                    sfm_images_dir,
                    artifacts.colmap_dir,
                    volume=volume,
                    pairs=pairs,
                    matching=LearnedMatchingConfig(extractor="superpoint"),
                )
                model = colmap.model
                diagnostics["sfm"] = colmap.diagnostics
            else:
                raise ValueError("sfm_matching must be 'exhaustive' or 'lightglue'")
        except Exception as e:
            colmap_error = str(e)
            diagnostics["colmap_error"] = colmap_error

    depth_bundle = _make_depth_estimators(
        volume=volume,
        depth_model=depth_model,
        depth_encoder=depth_encoder,
        depth_dataset=depth_dataset,
        depth_input_size=depth_input_size,
        max_depth_m=max_depth_m,
        depth_ensemble=depth_ensemble,
    )
    depth_estimators = depth_bundle["estimators"]
    diagnostics["depth_models"] = depth_bundle["diagnostics"]

    footprint_wkt = ""
    primary_footprint_src: Path | None = None
    if model is not None and colmap is not None:
        diagnostics["path"] = "colmap+metric-depth"

        # COLMAP can fragment into multiple sub-models. We support conservative aggregation.
        models = colmap.models or {"best": colmap.model}
        if multi_component == "best" or len(models) == 1:
            best_id = max(models.keys(), key=lambda k: len(models[k].images))
            models = {best_id: models[best_id]}

        # Component overlap heuristic: cluster components by image embedding similarity (to reduce double counting).
        comp_ids = sorted(models.keys())
        overlap_clusters: list[list[str]] = [[cid] for cid in comp_ids]
        overlap_diag: dict[str, object] = {"enabled": False}

        if multi_component == "sum" and len(comp_ids) > 1:
            try:
                backend = EmbeddingBackend(pair_embed)
                emb_all = compute_image_embeddings(pre.kept_images, backend=backend, device="cuda", batch_size=8)
                name_to_vec = {n: emb_all.embeddings[i] for i, n in enumerate(emb_all.image_names)}

                comp_vecs = []
                for cid in comp_ids:
                    img_names = [Path(im.name).name for im in models[cid].images.values()]
                    vecs = [name_to_vec[n] for n in img_names if n in name_to_vec]
                    if not vecs:
                        comp_vec = np.zeros((emb_all.embeddings.shape[1],), dtype=np.float32)
                    else:
                        comp_vec = np.mean(np.stack(vecs, axis=0), axis=0)
                    norm = float(np.linalg.norm(comp_vec))
                    if norm > 1e-9:
                        comp_vec = comp_vec / norm
                    comp_vecs.append(comp_vec.astype(np.float32))

                comp_mat = np.stack(comp_vecs, axis=0)
                sims = comp_mat @ comp_mat.T
                np.fill_diagonal(sims, -np.inf)
                # Threshold tuned for normalized torchvision/CLIP-like embeddings.
                sim_thresh = 0.45
                pairs = [(i, j) for i in range(len(comp_ids)) for j in range(i + 1, len(comp_ids)) if float(sims[i, j]) >= sim_thresh]
                comps = connected_components_from_pairs(len(comp_ids), pairs)
                overlap_clusters = [[comp_ids[i] for i in comp] for comp in comps]
                overlap_diag = {
                    "enabled": True,
                    "backend": str(pair_embed),
                    "sim_threshold": float(sim_thresh),
                    "clusters": overlap_clusters,
                }
            except Exception as e:
                overlap_diag = {"enabled": False, "error": str(e)}

        per_depth: list[dict[str, object]] = []
        depth_level_samples: list[np.ndarray] = []
        best_comp_sqft = -1.0
        best_comp_fp: Path | None = None

        for depth_key, depth_est in depth_estimators.items():
            comp_estimates: dict[str, GeometryEstimate] = {}
            for cid, m in models.items():
                comp_estimates[cid] = _run_colmap_geometry(
                    component_id=cid,
                    model=m,
                    depth_key=depth_key,
                    depth_est=depth_est,
                    artifacts=artifacts,
                    volume_dir=volume_dir,
                    sfm_images_dir=sfm_images_dir,
                    max_depth_m=max_depth_m,
                    pc_stride=pc_stride,
                    alpha=alpha,
                    fusion=fusion,
                    uncertainty=uncertainty,
                    mc_samples=mc_samples,
                )

            for est in comp_estimates.values():
                try:
                    fp = est.diagnostics.get("footprint", {}).get("footprint_path")
                    if fp and float(est.sqft) > float(best_comp_sqft):
                        best_comp_sqft = float(est.sqft)
                        best_comp_fp = Path(fp)
                except Exception:
                    pass

            # Combine across components for this depth model.
            component_rows = [
                {
                    "component_id": cid,
                    "sqft": est.sqft,
                    "interval_90": list(est.interval_90),
                    "confidence": est.confidence,
                    "samples_path": est.samples_path,
                    "diagnostics": est.diagnostics,
                }
                for cid, est in comp_estimates.items()
            ]

            # Point estimate: sum of max-per-overlap-cluster (reduces double counting).
            cluster_vals = [max((comp_estimates[c].sqft for c in cluster), default=0.0) for cluster in overlap_clusters]
            sqft_point = float(sum(cluster_vals))

            lo = float(max((comp_estimates[cid].interval_90[0] for cid in comp_estimates), default=0.0))
            hi = float(sum((comp_estimates[cid].interval_90[1] for cid in comp_estimates), 0.0))
            conf = float(min((comp_estimates[cid].confidence for cid in comp_estimates), default=0.0))

            samples_path = None
            if uncertainty == "montecarlo":
                # Combine via max-within-cluster then sum across clusters.
                rng = np.random.default_rng(0)
                n = int(mc_samples)
                samples = np.zeros((n,), dtype=np.float64)
                for i in range(n):
                    total = 0.0
                    for cluster in overlap_clusters:
                        vals = []
                        for cid in cluster:
                            sp = comp_estimates[cid].samples_path
                            if sp and Path(sp).exists():
                                arr = np.load(sp)
                                if arr.size > 0:
                                    vals.append(float(rng.choice(arr)))
                                else:
                                    vals.append(float(comp_estimates[cid].sqft))
                            else:
                                vals.append(float(comp_estimates[cid].sqft))
                        total += float(max(vals) if vals else 0.0)
                    samples[i] = total
                samples_file = volume_dir / f"samples_{_slug(depth_key)}_combined.npy"
                np.save(samples_file, samples.astype(np.float32))
                samples_path = str(samples_file)
                depth_level_samples.append(samples.astype(np.float64))
                sqft_point = float(np.median(samples))
                lo = float(np.percentile(samples, 5))
                hi = float(np.percentile(samples, 95))

            per_depth.append(
                {
                    "depth_key": depth_key,
                    "sqft": sqft_point,
                    "interval_90": [lo, hi],
                    "confidence": conf,
                    "samples_path": samples_path,
                    "components": component_rows,
                }
            )

        diagnostics["multi_component"] = {"mode": multi_component, "overlap": overlap_diag, "per_depth": per_depth}
        if best_comp_fp is not None and best_comp_fp.exists():
            primary_footprint_src = best_comp_fp
            diagnostics["primary_footprint"] = str(best_comp_fp)

        # Combine across depth models (mixture).
        if len(per_depth) == 1:
            chosen = per_depth[0]
            sqft = float(chosen["sqft"])
            lo = float(chosen["interval_90"][0])
            hi = float(chosen["interval_90"][1])
            conf = float(chosen["confidence"])
            area_m2 = sqft / 10.763910416709722
        else:
            if uncertainty == "montecarlo" and depth_level_samples:
                mix = np.concatenate(depth_level_samples, axis=0)
                samples_file = volume_dir / "samples_depth_ensemble_mixture.npy"
                np.save(samples_file, mix.astype(np.float32))
                sqft = float(np.median(mix))
                lo = float(np.percentile(mix, 5))
                hi = float(np.percentile(mix, 95))
                conf = float(min(d["confidence"] for d in per_depth))
                diagnostics["depth_ensemble"] = {"samples_path": str(samples_file)}
            else:
                ests = np.array([float(d["sqft"]) for d in per_depth], dtype=np.float64)
                sqft = float(np.median(ests))
                lo = float(min(float(d["interval_90"][0]) for d in per_depth))
                hi = float(max(float(d["interval_90"][1]) for d in per_depth))
                conf = float(min(float(d["confidence"]) for d in per_depth))
            area_m2 = sqft / 10.763910416709722
    else:
        # Fallback path(s)
        if colmap_error:
            diagnostics["sfm_error"] = colmap_error

        if fallback in {"dust3r", "mast3r"}:
            diagnostics["path"] = f"{fallback}+metric-depth"
            imgs = pre.kept_images
            if fallback == "dust3r":
                recon = run_dust3r_reconstruction(images=imgs, volume=volume, work_dir=work_dir, cfg=Dust3RConfig())
            else:
                recon = run_mast3r_reconstruction(images=imgs, volume=volume, work_dir=work_dir, cfg=MASt3RConfig())
            diagnostics[fallback] = recon.diagnostics

            per_depth = []
            best_fp: Path | None = None
            best_sqft = -1.0
            for depth_key, depth_est in depth_estimators.items():
                # Build metric depth maps (resized to match recon depth map shapes).
                metric_depths: list[np.ndarray] = []
                for img_path, du in zip(imgs[: len(recon.depthmaps)], recon.depthmaps, strict=False):
                    pred = depth_est.infer(img_path)
                    dm = pred.depth_m
                    if dm.shape != du.shape:
                        try:
                            import cv2

                            dm = cv2.resize(
                                dm.astype(np.float32), (du.shape[1], du.shape[0]), interpolation=cv2.INTER_LINEAR
                            )
                        except Exception:
                            dm = np.array(
                                np.asarray(
                                    __import__("PIL.Image", fromlist=["Image"]).Image.fromarray(dm).resize(
                                        (du.shape[1], du.shape[0])
                                    )
                                ),
                                dtype=np.float32,
                            )
                    metric_depths.append(dm.astype(np.float32))

                s = estimate_scale_from_depthmaps(
                    depth_units=recon.depthmaps[: len(metric_depths)],
                    depth_metric_m=metric_depths,
                    masks=recon.masks[: len(metric_depths)],
                    max_depth_m=max_depth_m,
                )

                # Build a point cloud by stacking masked per-image pts3d (already in world coords, up to scale).
                pts_list: list[np.ndarray] = []
                for pts_i, m_i in zip(recon.points_world, recon.masks, strict=True):
                    pts = pts_i[m_i]
                    if pts.size == 0:
                        continue
                    # Downsample for speed.
                    if pts.shape[0] > 250_000:
                        idx = np.random.default_rng(0).choice(pts.shape[0], size=250_000, replace=False)
                        pts = pts[idx]
                    pts_list.append(pts.astype(np.float64) * float(s.scale_m_per_unit))
                if not pts_list:
                    raise RuntimeError(f"{fallback} produced no valid 3D points.")
                points_world_m = np.concatenate(pts_list, axis=0)

                cam2world = recon.cam2world.copy()
                cam2world[:, :3, 3] *= float(s.scale_m_per_unit)
                camera_centers_m = cam2world[:, :3, 3].astype(np.float64)

                rotations_w2c = [cam2world[i, :3, :3].T.astype(np.float64) for i in range(cam2world.shape[0])]
                up = estimate_gravity_up(rotations_w2c)

                rng = np.random.default_rng(0)
                plane, inliers, plane_diag = find_floor_plane(
                    points_world_m,
                    camera_centers=camera_centers_m,
                    gravity_up=up,
                    distance_thresh=0.06,
                    rng=rng,
                )
                footprint = estimate_floor_footprint_sqft(points_world_m, plane, inliers, alpha=alpha)
                fp_path = volume_dir / f"footprint_{_slug(depth_key)}_{_slug(fallback)}.wkt"
                if footprint.polygon_wkt:
                    _write_text(fp_path, footprint.polygon_wkt)

                sqft_i = float(footprint.area_sqft)
                lo_i, hi_i = _estimate_interval_90(sqft_i, scale_rel_std=float(s.scale_rel_std), coverage_penalty=0.25)
                conf_i = float(
                    max(0.0, 1.0 - 4.0 * float(s.scale_rel_std)) * min(1.0, float(plane_diag["inlier_ratio"]) / 0.25)
                )

                if fp_path.exists() and sqft_i > best_sqft:
                    best_sqft = sqft_i
                    best_fp = fp_path

                per_depth.append(
                    {
                        "depth_key": depth_key,
                        "sqft": sqft_i,
                        "interval_90": [lo_i, hi_i],
                        "confidence": conf_i,
                        "scale": {"scale_m_per_unit": s.scale_m_per_unit, "scale_rel_std": s.scale_rel_std},
                        "plane_inlier_ratio": float(plane_diag["inlier_ratio"]),
                        "footprint_path": str(fp_path) if fp_path.exists() else None,
                    }
                )
            if best_fp is not None:
                primary_footprint_src = best_fp

            diagnostics["fallback_depth_models"] = per_depth
            if len(per_depth) == 1:
                sqft = float(per_depth[0]["sqft"])
                lo = float(per_depth[0]["interval_90"][0])
                hi = float(per_depth[0]["interval_90"][1])
                conf = float(per_depth[0]["confidence"])
            else:
                ests = np.array([float(d["sqft"]) for d in per_depth], dtype=np.float64)
                sqft = float(np.median(ests))
                lo = float(min(float(d["interval_90"][0]) for d in per_depth))
                hi = float(max(float(d["interval_90"][1]) for d in per_depth))
                conf = float(min(float(d["confidence"]) for d in per_depth))
            area_m2 = None
        else:
            diagnostics["path"] = "depth-only"

            # Infer depth for each preprocessed image and estimate visible floor patch area per image.
            candidates: list[tuple[float, str, dict[str, object], str]] = []
            for img_path in pre.kept_images:
                out_path = artifacts.depth_dir / f"{img_path.stem}_raw_depth_meter.npy"
                if out_path.exists():
                    depth_m = np.load(out_path)
                    pred_intr = None
                else:
                    # Depth-only uses the first depth estimator.
                    depth_est = next(iter(depth_estimators.values()))
                    pred = depth_est.infer(img_path)
                    np.save(out_path, pred.depth_m.astype(np.float32))
                    depth_m = pred.depth_m
                    pred_intr = pred.intrinsics
                try:
                    area_sqft, poly_wkt, diag = _single_view_area_sqft(
                        depth_m=depth_m,
                        intrinsics=pred_intr,
                        pc_stride=pc_stride,
                        max_depth_m=max_depth_m,
                        alpha=alpha,
                    )
                    room_sqft, prior_diag = _layout_prior_room_area_sqft(poly_wkt)
                    candidates.append(
                        (
                            area_sqft,
                            poly_wkt,
                            {**diag, "layout_prior_room_sqft": room_sqft, "layout_prior": prior_diag},
                            img_path.name,
                        )
                    )
                except Exception as e:
                    candidates.append((0.0, "", {"error": str(e)}, img_path.name))

            # Cluster images by similarity and sum per-cluster max room estimate.
            backend = EmbeddingBackend(pair_embed)
            emb = compute_image_embeddings(pre.kept_images, backend=backend, device="cuda", batch_size=8)
            sel = build_topk_pairs(emb.embeddings, k=min(10, max(1, len(pre.kept_images) - 1)), min_cosine_sim=0.25, mutual=True)
            comps = connected_components_from_pairs(len(pre.kept_images), sel.pairs)

            name_to_candidate = {name: (a, wkt, diag) for a, wkt, diag, name in candidates}
            cluster_rows = []
            cluster_sum = 0.0
            for comp in comps:
                names = [emb.image_names[i] for i in comp]
                room_sqfts = []
                for n in names:
                    diag = name_to_candidate.get(n, (0.0, "", {}))[2]
                    room_sqfts.append(float(diag.get("layout_prior_room_sqft") or 0.0))
                cluster_room = float(max(room_sqfts) if room_sqfts else 0.0)
                cluster_rows.append({"images": names, "room_sqft": cluster_room})
                cluster_sum += cluster_room

            best = max(candidates, key=lambda t: t[0]) if candidates else (0.0, "", {}, "")
            visible_sqft = float(best[0])
            footprint_wkt = best[1]
            diagnostics["depth_only"] = {
                "best_image": best[3],
                "visible_floor_sqft": visible_sqft,
                "clusters": cluster_rows,
                "pair_selection": sel.diagnostics,
                "per_image": [{"image": name, "visible_floor_sqft": a, "diag": d} for a, _, d, name in candidates],
            }

            # Apartment overhead factor (hallways/closets not captured in a single room estimate).
            overhead = 1.15 if cluster_sum > 0 else 3.5
            sqft = float(cluster_sum * overhead) if cluster_sum > 0 else float(visible_sqft * overhead)
            lo = max(0.0, visible_sqft)
            hi = max(lo, sqft * 2.0)
            conf = 0.08
            area_m2 = None

    if not footprint_wkt and primary_footprint_src is not None and primary_footprint_src.exists():
        try:
            footprint_wkt = primary_footprint_src.read_text(encoding="utf-8")
        except Exception:
            footprint_wkt = ""

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
            "footprint_wkt": str(volume_dir / "footprint.wkt"),
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
    downloads_dir: Path | None,
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
