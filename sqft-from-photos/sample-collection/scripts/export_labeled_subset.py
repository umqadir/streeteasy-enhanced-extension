#!/usr/bin/env python3

"""
Export a labeled-only subset of a `listings.json` dataset.

Purpose:
  Create a small eval JSON containing ONLY listings that have numeric `sqft`.
  This avoids confusion with `has_sqft_data` flags and makes downstream eval/sweeps cleaner.

Output keeps the same structure as the input dataset:
  {
    "dataset_info": {...},
    "listings": [...],
  }

It does NOT copy photos; `photo_paths` remain relative to the dataset root.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True, help="Path to listings.json")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: <dataset_dir>/listings_labeled_only.json)",
    )
    ap.add_argument(
        "--min-sqft",
        type=float,
        default=0.0,
        help="Optional sanity filter: drop sqft values smaller than this.",
    )
    ap.add_argument(
        "--max-sqft",
        type=float,
        default=100000.0,
        help="Optional sanity filter: drop sqft values larger than this.",
    )
    args = ap.parse_args()

    ds_path: Path = args.dataset
    out_path: Path = args.out or (ds_path.parent / "listings_labeled_only.json")

    ds = _read_json(ds_path)
    if not isinstance(ds, dict) or not isinstance(ds.get("listings"), list):
        raise SystemExit("Expected dataset JSON with top-level key: listings (list)")

    listings_in: list[dict[str, Any]] = [x for x in ds["listings"] if isinstance(x, dict)]
    listings_out: list[dict[str, Any]] = []

    for ex in listings_in:
        sqft = ex.get("sqft", None)
        if not isinstance(sqft, (int, float)):
            continue
        sqft_f = float(sqft)
        if sqft_f < float(args.min_sqft) or sqft_f > float(args.max_sqft):
            continue
        ex2 = dict(ex)
        ex2["sqft"] = sqft_f
        ex2["has_sqft_data"] = True
        listings_out.append(ex2)

    info = ds.get("dataset_info")
    if not isinstance(info, dict):
        info = {}
    info2 = dict(info)
    info2["exported_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    info2["exported_from"] = str(ds_path)
    info2["export_kind"] = "labeled_only"

    out = {"dataset_info": info2, "listings": listings_out}
    _write_json(out_path, out)
    print(json.dumps({"ok": True, "out": str(out_path), "n_labeled": len(listings_out)}, indent=2))


if __name__ == "__main__":
    main()

