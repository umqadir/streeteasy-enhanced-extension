from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.depth.base import DepthPrediction, PinholeIntrinsics


@dataclass(frozen=True)
class ZoeDepthConfig:
    hub_repo: str = "isl-org/ZoeDepth"
    hub_model: str = "ZoeD_NK"  # common metric model


class ZoeDepthMetric:
    """
    ZoeDepth metric depth via torch.hub.

    Notes:
    - This pulls code + weights at runtime the first time it is used (cached under TORCH_HOME).
    - For offline-ish runs, call it once during setup to warm caches.
    """

    def __init__(self, cfg: ZoeDepthConfig) -> None:
        self._cfg = cfg
        self._model = None
        self._device = "cpu"

    def ensure_available(self) -> None:
        # torch.hub handles fetching; nothing to check locally.
        return None

    def _load(self) -> None:
        try:
            import torch
        except Exception as e:  # pragma: no cover
            raise RuntimeError("Missing torch. In the pod: `cd cv-pipeline && uv sync --extra gpu`.") from e

        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = torch.hub.load(self._cfg.hub_repo, self._cfg.hub_model, pretrained=True)
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Failed to load ZoeDepth via torch.hub. "
                "Ensure the pod can reach GitHub and the ZoeDepth repo is accessible."
            ) from e

        model = model.to(device).eval()
        self._model = model
        self._device = device

    def infer(self, image_path: Path, *, intrinsics: PinholeIntrinsics | None = None) -> DepthPrediction:
        if self._model is None:
            self._load()

        import torch
        from PIL import Image

        img = Image.open(image_path).convert("RGB")

        with torch.inference_mode():
            # ZoeDepth hub models typically expose infer_pil.
            if hasattr(self._model, "infer_pil"):
                depth = self._model.infer_pil(img)  # type: ignore[attr-defined]
            else:  # pragma: no cover
                raise RuntimeError("ZoeDepth model missing infer_pil().")

        depth_m = np.asarray(depth, dtype=np.float32)
        return DepthPrediction(depth_m=depth_m, intrinsics=None, diagnostics={"hub_model": self._cfg.hub_model})

    def infer_to_npy(
        self, image_path: Path, out_path: Path, *, intrinsics: PinholeIntrinsics | None = None
    ) -> Path:
        if out_path.exists():
            return out_path
        pred = self.infer(image_path, intrinsics=intrinsics)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(out_path, pred.depth_m.astype(np.float32))
        return out_path

