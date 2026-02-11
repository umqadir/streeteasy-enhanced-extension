from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.depth.base import DepthPrediction, PinholeIntrinsics
from cv_pipeline.paths import VolumePaths


@dataclass(frozen=True)
class MoGeV2Config:
    repo: str = "Ruicheng/moge-2-vitl-normal"
    fp16: bool = True
    resolution_level: int = 9


class MoGeV2Metric:
    """
    MoGe-2 metric depth + intrinsics (FoV) prediction.

    Expects:
    - vendor repo cloned under: volume.vendor_dir / "moge"
    - HF snapshot downloaded (optional but recommended): `download_models.py moge ...`
    """

    def __init__(self, volume: VolumePaths, cfg: MoGeV2Config) -> None:
        self._volume = volume
        self._cfg = cfg
        self._model = None
        self._device = "cpu"

    @property
    def vendor_repo_dir(self) -> Path:
        return self._volume.vendor_dir / "moge"

    def ensure_available(self) -> None:
        if not self.vendor_repo_dir.exists():
            raise FileNotFoundError(
                f"Missing vendor repo: {self.vendor_repo_dir}. "
                "Run: `python cv-pipeline/scripts/download_models.py vendor-all`"
            )

    def _load(self) -> None:
        self.ensure_available()
        if str(self.vendor_repo_dir) not in sys.path:
            sys.path.insert(0, str(self.vendor_repo_dir))

        try:
            import torch
            from moge.model.v2 import MoGeModel  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "MoGe requires extra dependencies. In the pod: `cd cv-pipeline && uv sync --extra gpu --extra depth`."
            ) from e

        local_snapshot = self._volume.checkpoints_dir / "moge" / self._cfg.repo.replace("/", "__")
        local_model = local_snapshot / "model.pt"
        source = str(local_model) if local_model.exists() and local_model.stat().st_size > 1_000_000 else self._cfg.repo

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = MoGeModel.from_pretrained(source).to(device).eval()
        self._model = model
        self._device = device

    def _normalize_intrinsics(self, k: np.ndarray, *, w: int, h: int) -> PinholeIntrinsics | None:
        """
        MoGe returns "normalized" intrinsics. In most cases this means fx,fy,cx,cy are in [0,1] units.
        We convert to pixel-space if values look normalized.
        """
        if k.shape != (3, 3):
            return None
        fx, fy, cx, cy = float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2])
        # Heuristic: normalized intrinsics typically have fx~[0.5,2.0], cx~[0.4,0.6].
        if 0.0 < fx < 10.0 and 0.0 < fy < 10.0 and 0.0 < cx < 2.0 and 0.0 < cy < 2.0:
            fx *= float(w)
            fy *= float(h)
            cx *= float(w)
            cy *= float(h)
        return PinholeIntrinsics(fx=fx, fy=fy, cx=cx, cy=cy, width=int(w), height=int(h))

    def infer(self, image_path: Path, *, intrinsics: PinholeIntrinsics | None = None) -> DepthPrediction:
        if self._model is None:
            self._load()

        import cv2
        import torch

        bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError(f"Failed to read image: {image_path}")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img_t = torch.tensor(rgb / 255.0, dtype=torch.float32, device=self._device).permute(2, 0, 1)

        fov_x = None
        if intrinsics is not None:
            # MoGe expects horizontal FoV in degrees.
            fx = max(1e-9, float(intrinsics.fx))
            fov_x = float(2.0 * np.rad2deg(np.arctan(0.5 * float(w) / fx)))

        use_fp16 = bool(self._cfg.fp16 and self._device.startswith("cuda"))
        with torch.inference_mode():
            out = self._model.infer(
                img_t,
                fov_x=fov_x,
                apply_mask=True,
                num_tokens=None,
                resolution_level=int(self._cfg.resolution_level),
                use_fp16=use_fp16,
            )

        depth = out.get("depth")
        if depth is None:
            raise RuntimeError("MoGe did not return 'depth'.")
        depth_m = depth.detach().float().cpu().numpy()
        if depth_m.ndim == 3:
            depth_m = depth_m.squeeze()

        k_pred = out.get("intrinsics")
        intr_out = None
        if k_pred is not None:
            intr_out = self._normalize_intrinsics(k_pred.detach().float().cpu().numpy(), w=w, h=h)

        return DepthPrediction(
            depth_m=np.asarray(depth_m, dtype=np.float32),
            intrinsics=intr_out,
            diagnostics={
                "repo": self._cfg.repo,
                "device": self._device,
                "fp16": bool(use_fp16),
                "resolution_level": int(self._cfg.resolution_level),
                "fov_x_deg_in": fov_x,
            },
        )

    def infer_to_npy(
        self, image_path: Path, out_path: Path, *, intrinsics: PinholeIntrinsics | None = None
    ) -> Path:
        if out_path.exists():
            return out_path
        pred = self.infer(image_path, intrinsics=intrinsics)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(out_path, pred.depth_m.astype(np.float32))
        return out_path
