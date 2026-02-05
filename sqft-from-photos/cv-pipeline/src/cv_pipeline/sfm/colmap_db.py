from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np


_CAMERA_MODELS: dict[str, int] = {
    # Colmap camera model IDs (stable across COLMAP versions).
    "SIMPLE_PINHOLE": 0,
    "PINHOLE": 1,
    "SIMPLE_RADIAL": 2,
    "RADIAL": 3,
    "OPENCV": 4,
    "OPENCV_FISHEYE": 5,
    "FULL_OPENCV": 6,
    "FOV": 7,
    "SIMPLE_RADIAL_FISHEYE": 8,
    "RADIAL_FISHEYE": 9,
    "THIN_PRISM_FISHEYE": 10,
}


def _pair_id(image_id1: int, image_id2: int) -> int:
    """
    Pair ID used by COLMAP: min(id1,id2) * 2147483647 + max(id1,id2)
    """
    if image_id1 > image_id2:
        image_id1, image_id2 = image_id2, image_id1
    return int(image_id1) * 2147483647 + int(image_id2)


@dataclass(frozen=True)
class CameraSpec:
    model: str
    width: int
    height: int
    params: np.ndarray  # float64 1D
    prior_focal_length: bool = True


class ColmapDatabase:
    """
    Minimal COLMAP database writer (SQLite).

    This intentionally supports only what we need for SfM:
    - cameras, images, keypoints, matches
    - no descriptors (mapper does not require them if matches are present)
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._create_schema()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def commit(self) -> None:
        self._conn.commit()

    def _create_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE cameras (
              camera_id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
              model INTEGER NOT NULL,
              width INTEGER NOT NULL,
              height INTEGER NOT NULL,
              params BLOB NOT NULL,
              prior_focal_length INTEGER NOT NULL
            );

            CREATE TABLE images (
              image_id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
              name TEXT NOT NULL UNIQUE,
              camera_id INTEGER NOT NULL,
              prior_qw REAL,
              prior_qx REAL,
              prior_qy REAL,
              prior_qz REAL,
              prior_tx REAL,
              prior_ty REAL,
              prior_tz REAL,
              FOREIGN KEY(camera_id) REFERENCES cameras(camera_id)
            );

            CREATE TABLE keypoints (
              image_id INTEGER PRIMARY KEY NOT NULL,
              rows INTEGER NOT NULL,
              cols INTEGER NOT NULL,
              data BLOB NOT NULL,
              FOREIGN KEY(image_id) REFERENCES images(image_id) ON DELETE CASCADE
            );

            CREATE TABLE matches (
              pair_id INTEGER PRIMARY KEY NOT NULL,
              rows INTEGER NOT NULL,
              cols INTEGER NOT NULL,
              data BLOB NOT NULL
            );

            CREATE TABLE two_view_geometries (
              pair_id INTEGER PRIMARY KEY NOT NULL,
              rows INTEGER NOT NULL,
              cols INTEGER NOT NULL,
              data BLOB,
              config INTEGER NOT NULL,
              F BLOB,
              E BLOB,
              H BLOB,
              qvec BLOB,
              tvec BLOB
            );
            """
        )
        self._conn.commit()

    def add_camera(self, spec: CameraSpec) -> int:
        model_id = _CAMERA_MODELS.get(spec.model)
        if model_id is None:
            raise ValueError(f"Unsupported camera model: {spec.model}")
        params = np.asarray(spec.params, dtype=np.float64).ravel()
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO cameras(model,width,height,params,prior_focal_length) VALUES(?,?,?,?,?)",
            (model_id, int(spec.width), int(spec.height), params.tobytes(), 1 if spec.prior_focal_length else 0),
        )
        return int(cur.lastrowid)

    def add_image(self, name: str, camera_id: int) -> int:
        cur = self._conn.cursor()
        cur.execute("INSERT INTO images(name,camera_id) VALUES(?,?)", (name, int(camera_id)))
        return int(cur.lastrowid)

    def add_keypoints_xy(self, image_id: int, keypoints_xy: np.ndarray) -> None:
        """
        Insert keypoints for an image.

        COLMAP commonly stores keypoints as 6 floats: (x, y, a11, a12, a21, a22),
        where the last 4 encode the affine shape. Learned detectors often only
        provide (x, y), so we pad with an identity affine shape.
        """
        kps = np.asarray(keypoints_xy, dtype=np.float32)
        if kps.ndim != 2 or kps.shape[1] not in {2, 6}:
            raise ValueError("keypoints_xy must be (N,2) or (N,6)")
        if kps.shape[1] == 2:
            # a11,a12,a21,a22 = identity
            pad = np.tile(np.asarray([1.0, 0.0, 0.0, 1.0], dtype=np.float32)[None, :], (kps.shape[0], 1))
            kps = np.concatenate([kps, pad], axis=1)
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO keypoints(image_id,rows,cols,data) VALUES(?,?,?,?)",
            (int(image_id), int(kps.shape[0]), int(kps.shape[1]), kps.tobytes()),
        )

    def add_matches(self, image_id1: int, image_id2: int, matches: np.ndarray) -> None:
        m = np.asarray(matches, dtype=np.int32)
        if m.ndim != 2 or m.shape[1] != 2:
            raise ValueError("matches must be (K,2) int indices")
        pid = _pair_id(int(image_id1), int(image_id2))
        cur = self._conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO matches(pair_id,rows,cols,data) VALUES(?,?,?,?)",
            (int(pid), int(m.shape[0]), int(m.shape[1]), m.tobytes()),
        )
