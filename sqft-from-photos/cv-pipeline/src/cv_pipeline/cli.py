from __future__ import annotations

import argparse
import json
from pathlib import Path

from cv_pipeline.pipeline.runner import run_listing, run_streeteasy_eval


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cv-pipeline", description="sqft-from-photos CV pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run pipeline on a directory of images.")
    run.add_argument("--images", type=Path, required=True, help="Directory containing listing photos.")
    run.add_argument("--listing-id", type=str, default=None)
    run.add_argument("--label-sqft", type=float, default=None)
    run.add_argument("--out-json", type=Path, default=None, help="Optional explicit output JSON path.")

    run.add_argument("--max-side", type=int, default=1600)
    run.add_argument("--colmap", action="store_true", help="Enable COLMAP SfM (recommended).")
    run.add_argument("--depth-model", choices=["depth-anything-metric"], default="depth-anything-metric")
    run.add_argument("--depth-encoder", choices=["vits", "vitb", "vitl"], default="vitl")
    run.add_argument("--depth-dataset", choices=["hypersim", "vkitti"], default="hypersim")
    run.add_argument("--depth-input-size", type=int, default=518)

    run.add_argument("--max-depth-m", type=float, default=20.0)
    run.add_argument("--pc-stride", type=int, default=4)
    run.add_argument("--alpha", type=float, default=0.0, help="Alpha-shape alpha; 0 => auto.")

    run.set_defaults(func=_cmd_run)

    ev = sub.add_parser("eval-streeteasy", help="Run on sample-collection format and compute metrics.")
    ev.add_argument("--dataset", type=Path, required=True)
    ev.add_argument("--downloads", type=Path, required=True)
    ev.add_argument("--limit", type=int, default=0, help="0 => all listings")
    ev.add_argument("--out-json", type=Path, default=None, help="Optional explicit output JSON path.")
    ev.set_defaults(func=_cmd_eval)
    return p


def _cmd_run(args: argparse.Namespace) -> None:
    result = run_listing(
        images_dir=args.images,
        listing_id=args.listing_id,
        label_sqft=args.label_sqft,
        max_side=args.max_side,
        use_colmap=args.colmap,
        depth_model=args.depth_model,
        depth_encoder=args.depth_encoder,
        depth_dataset=args.depth_dataset,
        depth_input_size=args.depth_input_size,
        max_depth_m=args.max_depth_m,
        pc_stride=args.pc_stride,
        alpha=args.alpha,
        out_json=args.out_json,
    )
    print(json.dumps(result, indent=2))


def _cmd_eval(args: argparse.Namespace) -> None:
    result = run_streeteasy_eval(
        dataset_path=args.dataset,
        downloads_dir=args.downloads,
        limit=args.limit,
        out_json=args.out_json,
    )
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)

