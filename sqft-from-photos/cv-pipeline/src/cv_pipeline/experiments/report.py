from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cv_pipeline.calibration.conformal import ConformalIntervalCalibrator


@dataclass(frozen=True)
class EvalSummary:
    n: int
    n_labeled: int
    mae_sqft: float | None
    mape: float | None
    interval_coverage: float | None
    mean_interval_width: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "n": self.n,
            "n_labeled": self.n_labeled,
            "mae_sqft": self.mae_sqft,
            "mape": self.mape,
            "interval_coverage": self.interval_coverage,
            "mean_interval_width": self.mean_interval_width,
        }


def load_conformal_calibrator(path: Path) -> ConformalIntervalCalibrator:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "q" not in data:
        raise ValueError("Invalid conformal calibrator JSON (expected keys: alpha, q).")
    return ConformalIntervalCalibrator(alpha=float(data.get("alpha", 0.10)), q=float(data["q"]))


def summarize_eval_rows(
    rows: list[dict[str, object]],
    *,
    interval_key: str = "interval_90",
    pred_key: str = "pred_sqft",
    label_key: str = "label_sqft",
    calibrator: ConformalIntervalCalibrator | None = None,
) -> EvalSummary:
    labeled = []
    pred = []
    lo = []
    hi = []
    for r in rows:
        if not isinstance(r.get(label_key), (int, float)):
            continue
        if not isinstance(r.get(pred_key), (int, float)):
            continue
        interval = r.get(interval_key)
        if not (isinstance(interval, list) and len(interval) == 2):
            continue
        if not isinstance(interval[0], (int, float)) or not isinstance(interval[1], (int, float)):
            continue
        y = float(r[label_key])
        yhat = float(r[pred_key])
        a, b = float(interval[0]), float(interval[1])
        if calibrator is not None:
            a, b = calibrator.apply(a, b)
        labeled.append(y)
        pred.append(yhat)
        lo.append(a)
        hi.append(b)

    n = int(len(rows))
    n_labeled = int(len(labeled))
    if not labeled:
        return EvalSummary(n=n, n_labeled=0, mae_sqft=None, mape=None, interval_coverage=None, mean_interval_width=None)

    y = np.asarray(labeled, dtype=np.float64)
    yhat = np.asarray(pred, dtype=np.float64)
    lo_a = np.asarray(lo, dtype=np.float64)
    hi_a = np.asarray(hi, dtype=np.float64)

    mae = float(np.mean(np.abs(yhat - y)))
    mape = float(np.mean(np.abs(yhat - y) / np.maximum(y, 1e-9)))
    covered = (y >= lo_a) & (y <= hi_a)
    coverage = float(np.mean(covered.astype(np.float64)))
    width = float(np.mean(np.maximum(0.0, hi_a - lo_a)))

    return EvalSummary(
        n=n,
        n_labeled=n_labeled,
        mae_sqft=mae,
        mape=mape,
        interval_coverage=coverage,
        mean_interval_width=width,
    )

