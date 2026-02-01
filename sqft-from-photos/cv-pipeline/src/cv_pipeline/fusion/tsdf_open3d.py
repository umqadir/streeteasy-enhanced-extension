from __future__ import annotations

from pathlib import Path

import numpy as np

from cv_pipeline.sfm.colmap_model import ColmapModel


def tsdf_fuse_open3d(
    *,
    model: ColmapModel,
    images_dir: Path,
    depth_dir: Path,
    scale_m_per_sfm: float,
    voxel_length: float = 0.03,
    sdf_trunc: float = 0.10,
    max_depth_m: float = 20.0,
) -> tuple[np.ndarray, dict[str, object]]:
    """
    TSDF fusion using Open3D, returning a point cloud in world coordinates (meters).

    Requires `open3d` installed (`uv sync --extra open3d`).
    """
    try:
        import open3d as o3d
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Missing open3d. Install: `cd cv-pipeline && uv sync --extra open3d`.") from e

    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=float(voxel_length),
        sdf_trunc=float(sdf_trunc),
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )

    used = 0
    for img in model.images.values():
        stem = Path(img.name).stem
        depth_path = depth_dir / f"{stem}_raw_depth_meter.npy"
        img_path = images_dir / Path(img.name).name
        if not depth_path.exists() or not img_path.exists():
            continue

        cam = model.cameras[img.camera_id]
        fx, fy, cx, cy = cam.intrinsics_pinhole()
        intrinsic = o3d.camera.PinholeCameraIntrinsic(int(cam.width), int(cam.height), float(fx), float(fy), float(cx), float(cy))

        color = o3d.io.read_image(str(img_path))
        depth_m = np.load(depth_path).astype(np.float32)
        depth = o3d.geometry.Image(depth_m)
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            color,
            depth,
            depth_scale=1.0,
            depth_trunc=float(max_depth_m),
            convert_rgb_to_intensity=False,
        )

        r = img.rotation_matrix().astype(np.float64)  # world->cam
        t = img.tvec.astype(np.float64) * float(scale_m_per_sfm)  # world->cam translation in meters
        # cam->world:
        extrinsic = np.eye(4, dtype=np.float64)
        extrinsic[:3, :3] = r.T
        extrinsic[:3, 3] = (-r.T @ t).reshape(3)

        volume.integrate(rgbd, intrinsic, extrinsic)
        used += 1

    pcd = volume.extract_point_cloud()
    pts = np.asarray(pcd.points, dtype=np.float64)
    diag = {"used_images": int(used), "points": int(pts.shape[0]), "voxel_length": voxel_length, "sdf_trunc": sdf_trunc}
    return pts, diag

