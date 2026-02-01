from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class PinholeIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


@dataclass(frozen=True)
class DepthPrediction:
    depth_m: np.ndarray  # (H, W) float32 meters
    intrinsics: PinholeIntrinsics | None = None
    diagnostics: dict[str, object] | None = None


class DepthEstimator(Protocol):
    def ensure_available(self) -> None: ...

    def infer(self, image_path: Path, *, intrinsics: PinholeIntrinsics | None = None) -> DepthPrediction: ...

    def infer_to_npy(
        self, image_path: Path, out_path: Path, *, intrinsics: PinholeIntrinsics | None = None
    ) -> Path: ...

