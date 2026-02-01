from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.depth.base import DepthPrediction, PinholeIntrinsics
from cv_pipeline.paths import VolumePaths


@dataclass(frozen=True)
class Metric3DConfig:
    model: str = "vit_small"  # vit_small|vit_large
    input_h: int = 616  # vit default from upstream hubconf example
    input_w: int = 1064
    max_depth_m: float = 300.0


class Metric3DV2:
    """
    Metric3D v2 wrapper.

    Expects:
    - vendor repo cloned under: volume.vendor_dir / "metric3d"
    - checkpoint under: volume.checkpoints_dir / "metric3d" / "metric_depth_vit_{small|large}_800k.pth"

    Notes:
    - Metric3D depth is produced in a canonical camera space; de-canonicalization uses focal length.
      If intrinsics are unknown, pass COLMAP intrinsics when available for best results.
    """

    def __init__(self, volume: VolumePaths, cfg: Metric3DConfig) -> None:
        self._volume = volume
        self._cfg = cfg
        self._model = None
        self._device = "cpu"

    @property
    def vendor_repo_dir(self) -> Path:
        return self._volume.vendor_dir / "metric3d"

    @property
    def checkpoint_path(self) -> Path:
        if self._cfg.model not in {"vit_small", "vit_large"}:
            raise ValueError(f"Unsupported Metric3D model: {self._cfg.model}")
        name = "metric_depth_vit_small_800k.pth" if self._cfg.model == "vit_small" else "metric_depth_vit_large_800k.pth"
        return self._volume.checkpoints_dir / "metric3d" / name

    def ensure_available(self) -> None:
        if not self.vendor_repo_dir.exists():
            raise FileNotFoundError(
                f"Missing vendor repo: {self.vendor_repo_dir}. "
                "Run: `python cv-pipeline/scripts/download_models.py vendor-all`"
            )
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(
                f"Missing checkpoint: {self.checkpoint_path}. "
                f"Run: `python cv-pipeline/scripts/download_models.py metric3d --model {self._cfg.model.replace('vit_', 'vit_')}`"
            )

    def _load(self) -> None:
        self.ensure_available()
        if str(self.vendor_repo_dir) not in sys.path:
            sys.path.insert(0, str(self.vendor_repo_dir))

        try:
            import torch
            import hubconf as metric3d_hub  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Metric3D requires extra deps (mmengine, etc.). "
                "In the pod: `cd cv-pipeline && uv sync --extra gpu --extra depth`."
            ) from e

        fn_name = "metric3d_vit_small" if self._cfg.model == "vit_small" else "metric3d_vit_large"
        build_fn = getattr(metric3d_hub, fn_name, None)
        if build_fn is None:  # pragma: no cover
            raise RuntimeError(f"Metric3D hubconf missing function: {fn_name}")

        model = build_fn(pretrain=False)
        state = torch.load(self.checkpoint_path, map_location="cpu")
        if isinstance(state, dict) and "model_state_dict" in state:
            state = state["model_state_dict"]
        model.load_state_dict(state, strict=False)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device).eval()
        self._model = model
        self._device = device

    def infer(self, image_path: Path, *, intrinsics: PinholeIntrinsics | None = None) -> DepthPrediction:
        if self._model is None:
            self._load()

        import cv2
        import torch

        rgb_origin = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if rgb_origin is None:
            raise RuntimeError(f"Failed to read image: {image_path}")
        rgb_origin = rgb_origin[:, :, ::-1]  # BGR->RGB
        h0, w0 = rgb_origin.shape[:2]

        # Estimate focal if none provided (used only for de-canonicalization).
        if intrinsics is None:
            # 70deg is a reasonable phone-camera prior; callers should prefer COLMAP/UniDepth/MoGe intrinsics.
            f = 0.5 * float(w0) / float(np.tan(np.deg2rad(70.0) / 2.0))
            intrinsics = PinholeIntrinsics(fx=f, fy=f, cx=0.5 * w0, cy=0.5 * h0, width=w0, height=h0)

        # Keep ratio resize to fit input size.
        input_size = (int(self._cfg.input_h), int(self._cfg.input_w))
        scale = min(input_size[0] / float(h0), input_size[1] / float(w0))
        rgb = cv2.resize(rgb_origin, (int(round(w0 * scale)), int(round(h0 * scale))), interpolation=cv2.INTER_LINEAR)

        fx_scaled = intrinsics.fx * scale

        # Pad to input_size.
        padding_val = [123.675, 116.28, 103.53]  # ImageNet mean in [0,255]
        h, w = rgb.shape[:2]
        pad_h = input_size[0] - h
        pad_w = input_size[1] - w
        pad_h0 = pad_h // 2
        pad_w0 = pad_w // 2
        rgb = cv2.copyMakeBorder(
            rgb,
            pad_h0,
            pad_h - pad_h0,
            pad_w0,
            pad_w - pad_w0,
            cv2.BORDER_CONSTANT,
            value=padding_val,
        )
        pad_info = (pad_h0, pad_h - pad_h0, pad_w0, pad_w - pad_w0)

        # Normalize.
        mean = torch.tensor([123.675, 116.28, 103.53], dtype=torch.float32)[:, None, None]
        std = torch.tensor([58.395, 57.12, 57.375], dtype=torch.float32)[:, None, None]
        img_t = torch.from_numpy(rgb.transpose((2, 0, 1))).float()
        img_t = (img_t - mean) / std
        img_t = img_t[None].to(self._device)

        with torch.inference_mode():
            pred_depth, _confidence, _out = self._model.inference({"input": img_t})
        pred_depth = pred_depth.squeeze()

        # Unpad.
        pred_depth = pred_depth[pad_info[0] : pred_depth.shape[0] - pad_info[1], pad_info[2] : pred_depth.shape[1] - pad_info[3]]
        # Upsample to original.
        pred_depth = torch.nn.functional.interpolate(
            pred_depth[None, None, :, :], size=(h0, w0), mode="bilinear", align_corners=False
        ).squeeze()

        # De-canonicalize.
        canonical_to_real_scale = float(fx_scaled) / 1000.0
        pred_depth = pred_depth * canonical_to_real_scale
        pred_depth = torch.clamp(pred_depth, 0.0, float(self._cfg.max_depth_m))

        depth_m = pred_depth.detach().float().cpu().numpy()
        return DepthPrediction(
            depth_m=np.asarray(depth_m, dtype=np.float32),
            intrinsics=None,
            diagnostics={
                "model": self._cfg.model,
                "device": self._device,
                "input_size": {"h": int(self._cfg.input_h), "w": int(self._cfg.input_w)},
                "canonical_to_real_scale": canonical_to_real_scale,
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

