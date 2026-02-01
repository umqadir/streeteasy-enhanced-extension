from __future__ import annotations

import itertools
import json
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from cv_pipeline.dataset import load_streeteasy_dataset
from cv_pipeline.experiments.report import summarize_eval_rows
from cv_pipeline.paths import VolumePaths, default_volume_root
from cv_pipeline.pipeline.runner import run_listing
from cv_pipeline.utils.ids import new_run_id


def _stable_id(obj: object) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:10]


def _expand_grid(base: dict[str, Any], grid: dict[str, Any]) -> list[dict[str, Any]]:
    keys = sorted(grid.keys())
    values = []
    for k in keys:
        v = grid[k]
        if not isinstance(v, list) or not v:
            raise ValueError(f"grid[{k}] must be a non-empty list")
        values.append(v)
    runs: list[dict[str, Any]] = []
    for combo in itertools.product(*values):
        cfg = dict(base)
        for k, v in zip(keys, combo, strict=True):
            cfg[k] = v
        runs.append(cfg)
    return runs


def _normalize_run_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    de = out.get("depth_ensemble")
    if isinstance(de, str):
        out["depth_ensemble"] = [s.strip() for s in de.split(",") if s.strip()]
    elif de is None:
        out["depth_ensemble"] = None
    elif isinstance(de, list):
        out["depth_ensemble"] = [str(s).strip() for s in de if str(s).strip()]
    else:
        raise ValueError("depth_ensemble must be a list, string, or null")
    return out


def run_streeteasy_sweep(
    *,
    dataset_path: Path,
    downloads_dir: Path | None,
    config_path: Path | None,
    limit: int,
    out_json: Path | None,
) -> dict[str, object]:
    """
    Run a list/grid of run configurations over the Streeteasy sample-collection dataset.

    Config format (JSON):
      - {"runs": [ {run_listing kwargs...}, ... ]}
      - or {"base": {...}, "grid": {"depth_model": [...], "sfm_matching": [...], ...}}
    """
    cfg_obj: dict[str, Any] = {}
    if config_path is not None:
        cfg_obj = json.loads(config_path.read_text(encoding="utf-8"))

    if "runs" in cfg_obj:
        runs_raw = cfg_obj["runs"]
        if not isinstance(runs_raw, list) or not runs_raw:
            raise ValueError("config.runs must be a non-empty list")
        runs = [_normalize_run_cfg(dict(r)) for r in runs_raw]
    else:
        base = dict(cfg_obj.get("base", {})) if isinstance(cfg_obj.get("base", {}), dict) else {}
        grid = dict(cfg_obj.get("grid", {})) if isinstance(cfg_obj.get("grid", {}), dict) else {}
        if not grid:
            # Built-in default (small but useful)
            runs = [
                _normalize_run_cfg({"sfm_matching": "exhaustive", "depth_model": "depth-anything-metric", "fusion": "none", "uncertainty": "heuristic"}),
                _normalize_run_cfg({"sfm_matching": "lightglue", "depth_model": "metric3d-v2", "fusion": "tsdf", "uncertainty": "montecarlo"}),
                _normalize_run_cfg({"sfm_matching": "lightglue", "depth_model": "ensemble", "depth_ensemble": ["metric3d-v2", "unidepth-v1"], "fusion": "tsdf", "uncertainty": "montecarlo"}),
            ]
        else:
            runs = [_normalize_run_cfg(r) for r in _expand_grid(base, grid)]

    examples = load_streeteasy_dataset(dataset_path, downloads_dir)
    if limit and limit > 0:
        examples = examples[:limit]

    sweep_id = new_run_id(prefix="sweep")
    volume = VolumePaths(root=default_volume_root())
    out_path = out_json or (volume.runs_dir / sweep_id / "sweep.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run_summaries: list[dict[str, object]] = []
    for cfg in runs:
        run_cfg = {
            # Defaults (can be overridden in cfg)
            "max_side": 1600,
            "use_colmap": True,
            "sfm_matching": "exhaustive",
            "pair_embed": "torchvision-resnet50",
            "pair_topk": 10,
            "pair_min_sim": 0.2,
            "multi_component": "best",
            "depth_model": "depth-anything-metric",
            "depth_encoder": "vitl",
            "depth_dataset": "hypersim",
            "depth_input_size": 518,
            "depth_ensemble": None,
            "max_depth_m": 20.0,
            "pc_stride": 4,
            "alpha": 0.0,
            "fusion": "none",
            "uncertainty": "heuristic",
            "mc_samples": 200,
            "fallback": "depth-only",
        }
        run_cfg.update(cfg)
        cfg_id = _stable_id(run_cfg)

        rows: list[dict[str, object]] = []
        for ex in examples:
            if not ex.images_dir.exists():
                rows.append(
                    {
                        "listing_id": ex.listing_id,
                        "listing_url": ex.listing_url,
                        "label_sqft": ex.sqft,
                        "error": f"missing images_dir: {ex.images_dir}",
                    }
                )
                continue
            try:
                res = run_listing(
                    images_dir=ex.images_dir,
                    listing_id=ex.listing_id,
                    label_sqft=ex.sqft,
                    out_json=None,
                    **run_cfg,
                )
                rows.append(
                    {
                        "listing_id": ex.listing_id,
                        "listing_url": ex.listing_url,
                        "label_sqft": ex.sqft,
                        "pred_sqft": res["sqft_estimate"],
                        "interval_90": res["sqft_interval_90"],
                        "confidence": res["confidence_score"],
                        "run_id": res["run_id"],
                        "cfg_id": cfg_id,
                    }
                )
            except Exception as e:
                rows.append(
                    {
                        "listing_id": ex.listing_id,
                        "listing_url": ex.listing_url,
                        "label_sqft": ex.sqft,
                        "error": str(e),
                        "cfg_id": cfg_id,
                    }
                )

        summary = summarize_eval_rows(rows, interval_key="interval_90", pred_key="pred_sqft", label_key="label_sqft").to_dict()
        run_summaries.append(
            {
                "cfg_id": cfg_id,
                "run_cfg": run_cfg,
                "metrics": summary,
                "rows": rows,
            }
        )

    sweep_summary = {
        "sweep_id": sweep_id,
        "dataset": str(dataset_path),
        "downloads": str(downloads_dir),
        "n_runs": len(run_summaries),
        "runs": run_summaries,
    }
    out_path.write_text(json.dumps(sweep_summary, indent=2), encoding="utf-8")
    return sweep_summary
