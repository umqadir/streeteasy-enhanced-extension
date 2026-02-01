from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ConformalIntervalCalibrator:
    """
    Simple post-hoc interval calibrator:
    expand [lo, hi] by a learned nonconformity quantile q so that empirical coverage ~= target.
    """

    alpha: float  # e.g., 0.10 for 90% interval
    q: float

    def apply(self, lo: float, hi: float) -> tuple[float, float]:
        q = float(max(0.0, self.q))
        return float(lo - q), float(hi + q)


def fit_conformal_interval_calibrator(
    *,
    y_true: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    alpha: float,
) -> ConformalIntervalCalibrator:
    y = np.asarray(y_true, dtype=np.float64)
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    if y.shape != lo.shape or y.shape != hi.shape:
        raise ValueError("y_true, lo, hi must have the same shape")
    if not (0.0 < float(alpha) < 1.0):
        raise ValueError("alpha must be in (0,1)")

    # Nonconformity score: how far outside the interval the label lies (0 if inside).
    s = np.maximum.reduce([lo - y, y - hi, np.zeros_like(y)])
    s = s[np.isfinite(s)]
    if s.size == 0:
        raise ValueError("No finite calibration scores")

    q = float(np.quantile(s, 1.0 - float(alpha), method="higher"))
    return ConformalIntervalCalibrator(alpha=float(alpha), q=q)

