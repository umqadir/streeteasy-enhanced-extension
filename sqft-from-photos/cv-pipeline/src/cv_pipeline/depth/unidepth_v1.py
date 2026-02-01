from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.depth.base import DepthPrediction, PinholeIntrinsics
from cv_pipeline.paths import VolumePaths


@dataclass(frozen=True)
class UniDepthV1Config:
    repo: str = "lpiccinelli/unidepth-v1-vitl14"


class UniDepthV1Metric:
    """
    UniDepthV1 metric depth + intrinsics (optional) inference.

    Expects:
    - vendor repo cloned under: volume.vendor_dir / "unidepth"
    - HF snapshot downloaded (optional but recommended): `download_models.py unidepth ...`
    """

    def __init__(self, volume: VolumePaths, cfg: UniDepthV1Config) -> None:
        self._volume = volume
        self._cfg = cfg
        self._model = None
        self._device = "cpu"

    @property
    def vendor_repo_dir(self) -> Path:
        return self._volume.vendor_dir / "unidepth"

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
            from unidepth.models import UniDepthV1  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "UniDepth requires extra dependencies. In the pod: `cd cv-pipeline && uv sync --extra gpu --extra depth`."
            ) from e

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = UniDepthV1.from_pretrained(self._cfg.repo).to(device).eval()
        self._model = model
        self._device = device

    def infer(self, image_path: Path, *, intrinsics: PinholeIntrinsics | None = None) -> DepthPrediction:
        """
        Returns:
        - depth (meters)
        - predicted intrinsics (if provided by model)

        If `intrinsics` is provided, UniDepth will use it; otherwise it may predict intrinsics.
        """
        if self._model is None:
            self._load()

        import torch
        from PIL import Image

        arr = np.asarray(Image.open(image_path).convert("RGB"))
        rgb = torch.from_numpy(arr).permute(2, 0, 1).to(self._device)

        cam = None
        if intrinsics is not None:
            k = torch.tensor(
                [[intrinsics.fx, 0.0, intrinsics.cx], [0.0, intrinsics.fy, intrinsics.cy], [0.0, 0.0, 1.0]],
                dtype=torch.float32,
                device=self._device,
            )
            cam = k

        with torch.inference_mode():
            preds = self._model.infer(rgb, cam) if cam is not None else self._model.infer(rgb)

        depth = preds.get("depth")
        if depth is None:
            raise RuntimeError("UniDepth did not return 'depth'.")
        depth_m = depth.detach().float().cpu().numpy()

        k_pred = preds.get("intrinsics")
        intr_out: PinholeIntrinsics | None = None
        if k_pred is not None:
            k_np = k_pred.detach().float().cpu().numpy()
            h, w = depth_m.shape[:2]
            intr_out = PinholeIntrinsics(
                fx=float(k_np[0, 0]),
                fy=float(k_np[1, 1]),
                cx=float(k_np[0, 2]),
                cy=float(k_np[1, 2]),
                width=int(w),
                height=int(h),
            )

        return DepthPrediction(
            depth_m=depth_m.astype(np.float32),
            intrinsics=intr_out,
            diagnostics={"repo": self._cfg.repo, "device": self._device},
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

