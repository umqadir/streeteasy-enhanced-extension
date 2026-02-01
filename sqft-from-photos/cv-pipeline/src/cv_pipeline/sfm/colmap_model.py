from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class ColmapCamera:
    camera_id: int
    model: str
    width: int
    height: int
    params: list[float]

    def intrinsics_pinhole(self) -> tuple[float, float, float, float]:
        """
        Returns (fx, fy, cx, cy) in pixels.

        Distortion parameters (if any) are ignored by v0 geometry.
        """
        m = self.model.upper()
        p = self.params
        if m in {"SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"}:
            f, cx, cy = p[0], p[1], p[2]
            return float(f), float(f), float(cx), float(cy)
        if m in {"PINHOLE", "OPENCV", "OPENCV_FISHEYE"}:
            fx, fy, cx, cy = p[0], p[1], p[2], p[3]
            return float(fx), float(fy), float(cx), float(cy)
        raise ValueError(f"Unsupported COLMAP camera model for v0: {self.model}")


@dataclass(frozen=True)
class ColmapImage:
    image_id: int
    name: str
    camera_id: int
    qvec: np.ndarray  # (4,)
    tvec: np.ndarray  # (3,)
    xys: np.ndarray  # (N,2) float
    point3d_ids: np.ndarray  # (N,) int

    def rotation_matrix(self) -> np.ndarray:
        """
        COLMAP: qvec is (qw,qx,qy,qz), representing world->cam rotation.
        """
        qw, qx, qy, qz = [float(x) for x in self.qvec]
        # Standard quaternion to rotation matrix.
        return np.array(
            [
                [1 - 2 * (qy**2 + qz**2), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
                [2 * (qx * qy + qz * qw), 1 - 2 * (qx**2 + qz**2), 2 * (qy * qz - qx * qw)],
                [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx**2 + qy**2)],
            ],
            dtype=np.float64,
        )

    def camera_center(self) -> np.ndarray:
        r = self.rotation_matrix()
        # COLMAP world->cam: x_cam = R x_world + t; so camera center C = -R^T t.
        return -r.T @ self.tvec.astype(np.float64)

    def world_to_cam(self, xyz_world: np.ndarray) -> np.ndarray:
        r = self.rotation_matrix()
        return (r @ xyz_world.T).T + self.tvec.astype(np.float64)


@dataclass(frozen=True)
class ColmapPoint3D:
    point3d_id: int
    xyz: np.ndarray  # (3,)
    error: float


@dataclass(frozen=True)
class ColmapModel:
    cameras: dict[int, ColmapCamera]
    images: dict[int, ColmapImage]
    points3d: dict[int, ColmapPoint3D]


def _iter_data_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def load_colmap_model_txt(model_dir: Path) -> ColmapModel:
    cams_path = model_dir / "cameras.txt"
    imgs_path = model_dir / "images.txt"
    pts_path = model_dir / "points3D.txt"

    cameras: dict[int, ColmapCamera] = {}
    for line in _iter_data_lines(cams_path):
        parts = line.split()
        camera_id = int(parts[0])
        model = parts[1]
        width = int(parts[2])
        height = int(parts[3])
        params = [float(x) for x in parts[4:]]
        cameras[camera_id] = ColmapCamera(camera_id=camera_id, model=model, width=width, height=height, params=params)

    # images.txt is a two-line-per-image format; the second line may be empty.
    # Parse it without dropping blank lines.
    raw_image_lines = imgs_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    images: dict[int, ColmapImage] = {}
    i = 0
    while i < len(raw_image_lines):
        line = raw_image_lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        meta = line.split()
        if len(meta) < 10:
            raise ValueError(f"Malformed images.txt line: {raw_image_lines[i]}")
        image_id = int(meta[0])
        qvec = np.array([float(x) for x in meta[1:5]], dtype=np.float64)
        tvec = np.array([float(x) for x in meta[5:8]], dtype=np.float64)
        camera_id = int(meta[8])
        name = " ".join(meta[9:])

        points_line_raw = raw_image_lines[i + 1] if i + 1 < len(raw_image_lines) else ""
        tokens = points_line_raw.strip().split()
        xs: list[float] = []
        ys: list[float] = []
        pids: list[int] = []
        if tokens:
            if len(tokens) % 3 != 0:
                raise ValueError(f"Malformed 2D points line in images.txt for image_id={image_id}")
            for j in range(0, len(tokens), 3):
                xs.append(float(tokens[j]))
                ys.append(float(tokens[j + 1]))
                pids.append(int(tokens[j + 2]))

        xys = np.stack([xs, ys], axis=1).astype(np.float64) if xs else np.zeros((0, 2), dtype=np.float64)
        point3d_ids = np.array(pids, dtype=np.int64) if pids else np.zeros((0,), dtype=np.int64)

        images[image_id] = ColmapImage(
            image_id=image_id,
            name=name,
            camera_id=camera_id,
            qvec=qvec,
            tvec=tvec,
            xys=xys,
            point3d_ids=point3d_ids,
        )
        i += 2

    points3d: dict[int, ColmapPoint3D] = {}
    for line in _iter_data_lines(pts_path):
        parts = line.split()
        point_id = int(parts[0])
        xyz = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=np.float64)
        error = float(parts[7])
        points3d[point_id] = ColmapPoint3D(point3d_id=point_id, xyz=xyz, error=error)

    return ColmapModel(cameras=cameras, images=images, points3d=points3d)
