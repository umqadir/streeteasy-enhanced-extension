from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.depth.base import DepthPrediction, PinholeIntrinsics
from cv_pipeline.paths import VolumePaths, ensure_dirs


@dataclass(frozen=True)
class DepthConfig:
    encoder: str = "vitl"
    dataset: str = "hypersim"
    input_size: int = 518
    max_depth_m: float = 20.0


class DepthAnythingV2Metric:
    """
    Wrapper around the official Depth-Anything-V2 metric_depth codebase.

    Expects:
    - vendor repo cloned under: volume.vendor_dir / "depth-anything-v2"
    - checkpoint under: volume.checkpoints_dir / f"depth_anything_v2_metric_{dataset}_{encoder}.pth"
    """

    def __init__(self, volume: VolumePaths, cfg: DepthConfig) -> None:
        self._volume = volume
        self._cfg = cfg
        self._model = None

    @property
    def vendor_repo_dir(self) -> Path:
        return self._volume.vendor_dir / "depth-anything-v2"

    @property
    def metric_depth_dir(self) -> Path:
        return self.vendor_repo_dir / "metric_depth"

    @property
    def checkpoint_path(self) -> Path:
        return (
            self._volume.checkpoints_dir
            / f"depth_anything_v2_metric_{self._cfg.dataset}_{self._cfg.encoder}.pth"
        )

    def ensure_available(self) -> None:
        if not self.vendor_repo_dir.exists():
            raise FileNotFoundError(
                f"Missing vendor repo: {self.vendor_repo_dir}. "
                f"Run: `python cv-pipeline/scripts/download_models.py depth-anything-metric`"
            )
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(
                f"Missing checkpoint: {self.checkpoint_path}. "
                f"Run: `python cv-pipeline/scripts/download_models.py depth-anything-metric "
                f"--encoder {self._cfg.encoder} --dataset {self._cfg.dataset}`"
            )

    def _load(self):
        self.ensure_available()
        ensure_dirs(self._volume.checkpoints_dir)

        try:
            import cv2  # noqa: F401
            import torch
        except Exception as e:  # pragma: no cover
            raise RuntimeError("DepthAnythingV2Metric requires opencv-python and torch.") from e

        metric_path = str(self.metric_depth_dir)
        if metric_path not in sys.path:
            sys.path.insert(0, metric_path)

        from depth_anything_v2.dpt import DepthAnythingV2  # type: ignore

        model_configs = {
            "vits": {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]},
            "vitb": {"encoder": "vitb", "features": 128, "out_channels": [96, 192, 384, 768]},
            "vitl": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]},
            "vitg": {"encoder": "vitg", "features": 384, "out_channels": [1536, 1536, 1536, 1536]},
        }

        if self._cfg.encoder not in model_configs:
            raise ValueError(f"Unsupported encoder: {self._cfg.encoder}")

        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

        model = DepthAnythingV2(**{**model_configs[self._cfg.encoder], "max_depth": self._cfg.max_depth_m})
        state = torch.load(self.checkpoint_path, map_location="cpu")
        model.load_state_dict(state)
        model = model.to(device).eval()
        self._model = model
        self._device = device

    def infer(self, image_path: Path, *, intrinsics: PinholeIntrinsics | None = None) -> DepthPrediction:
        """
        Returns metric depth (meters). Ignores intrinsics (this model does not need them).
        """
        if self._model is None:
            self._load()

        import cv2

        raw = cv2.imread(str(image_path))
        if raw is None:
            raise RuntimeError(f"Failed to read image: {image_path}")

        depth = self._model.infer_image(raw, self._cfg.input_size)  # HxW meters (numpy)
        depth = np.asarray(depth, dtype=np.float32)
        return DepthPrediction(depth_m=depth, intrinsics=None, diagnostics={"input_size": self._cfg.input_size})

    def infer_to_npy(
        self, image_path: Path, out_path: Path, *, intrinsics: PinholeIntrinsics | None = None
    ) -> Path:
        """
        Saves raw depth (meters) as .npy.
        """
        if out_path.exists():
            return out_path
        pred = self.infer(image_path, intrinsics=intrinsics)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(out_path, pred.depth_m.astype(np.float32))
        return out_path
