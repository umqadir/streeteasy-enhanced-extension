from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from cv_pipeline.calibration import fit_conformal_interval_calibrator
from cv_pipeline.dataset import load_streeteasy_dataset
from cv_pipeline.experiments.curate import curate_streeteasy
from cv_pipeline.experiments.report import load_conformal_calibrator, summarize_eval_rows
from cv_pipeline.experiments.sweep import run_streeteasy_sweep
from cv_pipeline.image.selection import ImageSelectionSpec, parse_filter_file
from cv_pipeline.image.preprocess import list_images
from cv_pipeline.paths import default_streeteasy_dataset_path
from cv_pipeline.pipeline.runner import run_listing, run_streeteasy_eval


def _build_parser() -> argparse.ArgumentParser:
    default_dataset = default_streeteasy_dataset_path()

    def _add_dataset_arg(sp: argparse.ArgumentParser) -> None:
        default_text = str(default_dataset) if default_dataset else "none found"
        sp.add_argument(
            "--dataset",
            type=Path,
            default=default_dataset,
            help="Path to StreetEasy listings JSON. "
            f"Default resolves to latest export/source dataset ({default_text}).",
        )

    p = argparse.ArgumentParser(prog="cv-pipeline", description="sqft-from-photos CV pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list-images", help="List images under a directory with stable indices.")
    ls.add_argument("--images", type=Path, required=True, help="Directory containing listing photos.")
    ls.add_argument("--out", type=Path, default=None, help="Optional path to write the listing to (text).")
    ls.set_defaults(func=_cmd_list_images)

    lsd = sub.add_parser("list-streeteasy", help="List listings from a Streeteasy dataset (IDs, urls, sqft).")
    _add_dataset_arg(lsd)
    lsd.add_argument(
        "--downloads",
        type=Path,
        default=None,
        help="Directory containing downloaded listing photo folders. Optional for datasets that include photo paths.",
    )
    lsd.add_argument("--has-sqft", action="store_true", help="Only show listings with ground-truth sqft.")
    lsd.add_argument("--limit", type=int, default=0, help="0 => all listings")
    lsd.set_defaults(func=_cmd_list_streeteasy)

    run = sub.add_parser("run", help="Run pipeline on a directory of images.")
    run.add_argument("--images", type=Path, required=True, help="Directory containing listing photos.")
    run.add_argument("--listing-id", type=str, default=None)
    run.add_argument("--label-sqft", type=float, default=None)
    run.add_argument("--out-json", type=Path, default=None, help="Optional explicit output JSON path.")

    run.add_argument("--filter-file", type=Path, default=None, help="Optional text file with include/exclude rules.")
    run.add_argument(
        "--include-glob",
        action="append",
        default=[],
        help="Glob (relative to --images) to include. Repeatable.",
    )
    run.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Glob (relative to --images) to exclude. Repeatable.",
    )
    run.add_argument(
        "--include-indices",
        type=str,
        default=None,
        help="Comma-separated indices/ranges (e.g. '0-10,12') in the order of `cv-pipeline contact-sheet` / list_images().",
    )
    run.add_argument("--exclude-indices", type=str, default=None)
    run.add_argument(
        "--include-file",
        type=Path,
        default=None,
        help="Newline-separated basenames or relative paths to include (lines starting with # ignored).",
    )
    run.add_argument(
        "--exclude-file",
        type=Path,
        default=None,
        help="Newline-separated basenames or relative paths to exclude (lines starting with # ignored).",
    )

    run.add_argument("--max-side", type=int, default=1600)
    run.add_argument("--colmap", action="store_true", help="Enable COLMAP SfM (recommended).")
    run.add_argument(
        "--sfm-matching",
        choices=["exhaustive", "lightglue"],
        default="exhaustive",
        help="When --colmap is enabled, choose matching backend.",
    )
    run.add_argument(
        "--multi-component",
        choices=["best", "sum"],
        default="best",
        help="If COLMAP fragments into multiple sub-models, use only the best or sum all components.",
    )
    run.add_argument(
        "--pair-embed",
        choices=["torchvision-resnet50", "torchvision-vit-b-16", "dinov2-vits14"],
        default="torchvision-resnet50",
        help="Embedding model used to pick candidate image pairs (LightGlue only).",
    )
    run.add_argument("--pair-topk", type=int, default=10)
    run.add_argument("--pair-min-sim", type=float, default=0.2)
    run.add_argument(
        "--depth-model",
        choices=["depth-anything-metric", "metric3d-v2", "unidepth-v1", "moge-v2", "zoedepth", "ensemble"],
        default="depth-anything-metric",
    )
    run.add_argument(
        "--depth-ensemble",
        type=str,
        default=None,
        help="Comma-separated depth models when --depth-model=ensemble "
        "(e.g. 'metric3d-v2,unidepth-v1,depth-anything-metric').",
    )
    run.add_argument("--depth-encoder", choices=["vits", "vitb", "vitl"], default="vitl")
    run.add_argument("--depth-dataset", choices=["hypersim", "vkitti"], default="hypersim")
    run.add_argument("--depth-input-size", type=int, default=518)

    run.add_argument("--max-depth-m", type=float, default=20.0)
    run.add_argument("--pc-stride", type=int, default=4)
    run.add_argument("--alpha", type=float, default=0.0, help="Alpha-shape alpha; 0 => auto.")
    run.add_argument("--fusion", choices=["none", "tsdf"], default="none")
    run.add_argument("--uncertainty", choices=["heuristic", "montecarlo"], default="heuristic")
    run.add_argument("--mc-samples", type=int, default=200)
    run.add_argument("--fallback", choices=["depth-only", "dust3r", "mast3r"], default="depth-only")

    run.set_defaults(func=_cmd_run)

    ev = sub.add_parser("eval-streeteasy", help="Run on sample-collection format and compute metrics.")
    _add_dataset_arg(ev)
    ev.add_argument(
        "--downloads",
        type=Path,
        default=None,
        help="Directory containing downloaded listing photo folders. Optional for datasets that include photo paths.",
    )
    ev.add_argument("--limit", type=int, default=0, help="0 => all listings")
    ev.add_argument("--has-sqft", action="store_true", help="Only evaluate listings with ground-truth sqft.")
    ev.add_argument(
        "--listing-ids",
        type=str,
        default=None,
        help="Comma-separated listing IDs to run (applied before --limit).",
    )
    ev.add_argument(
        "--filters-dir",
        type=Path,
        default=None,
        help="Optional directory containing per-listing filter files: <filters_dir>/<listing_id>.txt",
    )
    ev.add_argument("--out-json", type=Path, default=None, help="Optional explicit output JSON path.")
    ev.set_defaults(func=_cmd_eval)

    cur = sub.add_parser("curate-streeteasy", help="Create per-listing filter files + contact sheets (no GUI).")
    _add_dataset_arg(cur)
    cur.add_argument(
        "--downloads",
        type=Path,
        default=None,
        help="Directory containing downloaded listing photo folders. Optional for datasets that include photo paths.",
    )
    cur.add_argument("--out-dir", type=Path, default=None, help="Output directory (defaults under CVP_VOLUME).")
    cur.add_argument("--limit", type=int, default=0, help="0 => all listings")
    cur.add_argument("--has-sqft", action="store_true", help="Only include listings with ground-truth sqft.")
    cur.add_argument(
        "--listing-ids",
        type=str,
        default=None,
        help="Comma-separated listing IDs to curate (applied before --limit).",
    )
    cur.add_argument("--overwrite", action="store_true", help="Overwrite existing files under out-dir.")
    cur.add_argument("--cols", type=int, default=6, help="Contact sheet columns.")
    cur.add_argument("--thumb-size", type=int, default=256, help="Contact sheet thumbnail size.")
    cur.add_argument("--max-images", type=int, default=120, help="Max images rendered into the contact sheet.")
    cur.set_defaults(func=_cmd_curate)

    sweep = sub.add_parser("sweep-streeteasy", help="Run a set/grid of configs over the Streeteasy dataset.")
    _add_dataset_arg(sweep)
    sweep.add_argument(
        "--downloads",
        type=Path,
        default=None,
        help="Directory containing downloaded listing photo folders. Optional for datasets that include photo paths.",
    )
    sweep.add_argument("--config", type=Path, default=None, help="Optional JSON config (runs/grid).")
    sweep.add_argument("--limit", type=int, default=0, help="0 => all listings")
    sweep.add_argument("--has-sqft", action="store_true", help="Only evaluate listings with ground-truth sqft.")
    sweep.add_argument(
        "--listing-ids",
        type=str,
        default=None,
        help="Comma-separated listing IDs to run (applied before --limit).",
    )
    sweep.add_argument(
        "--filters-dir",
        type=Path,
        default=None,
        help="Optional directory containing per-listing filter files: <filters_dir>/<listing_id>.txt",
    )
    sweep.add_argument("--out-json", type=Path, default=None, help="Optional explicit output JSON path.")
    sweep.set_defaults(func=_cmd_sweep)

    cal = sub.add_parser("calibrate", help="Fit a simple conformal interval calibrator from eval JSON.")
    cal.add_argument("--eval-json", type=Path, required=True)
    cal.add_argument("--alpha", type=float, default=0.10, help="Target miscoverage (0.10 => 90% intervals).")
    cal.add_argument("--out-json", type=Path, default=None)
    cal.set_defaults(func=_cmd_calibrate)

    rep = sub.add_parser("report-eval", help="Summarize an eval JSON (optionally applying a calibration file).")
    rep.add_argument("--eval-json", type=Path, required=True)
    rep.add_argument("--calibration-json", type=Path, default=None)
    rep.add_argument("--out-json", type=Path, default=None)
    rep.set_defaults(func=_cmd_report_eval)
    return p


def _cmd_list_images(args: argparse.Namespace) -> None:
    imgs = list_images(args.images)
    lines = []
    for i, p in enumerate(imgs):
        try:
            rel = p.relative_to(args.images).as_posix()
        except Exception:
            rel = p.name
        lines.append(f"{i:04d}\t{rel}")
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")


def _cmd_list_streeteasy(args: argparse.Namespace) -> None:
    if not args.dataset:
        raise SystemExit("No dataset provided and no default dataset found.")
    # Also show the dataset's "flag" field (has_sqft_data), because some datasets
    # may have has_sqft_data=true but still be missing numeric sqft.
    raw = json.loads(args.dataset.read_text(encoding="utf-8"))
    flag_by_id: dict[str, bool] = {}
    if isinstance(raw, dict) and isinstance(raw.get("listings"), list):
        for item in raw["listings"]:
            if not isinstance(item, dict):
                continue
            listing_id = str(item.get("id") or "").strip()
            if not listing_id:
                continue
            flag_by_id[listing_id] = bool(item.get("has_sqft_data")) if "has_sqft_data" in item else False

    examples = load_streeteasy_dataset(args.dataset, args.downloads)
    if args.has_sqft:
        examples = [ex for ex in examples if isinstance(ex.sqft, (int, float))]
    if args.limit and args.limit > 0:
        examples = examples[: args.limit]

    rows = []
    for ex in examples:
        flag = 1 if flag_by_id.get(ex.listing_id, False) else 0
        sqft = "" if ex.sqft is None else f"{float(ex.sqft):.0f}"
        rows.append(f"{ex.listing_id}\tflag={flag}\tsqft={sqft}\t{ex.images_dir}\t{ex.listing_url}")
    print("\n".join(rows))


def _cmd_run(args: argparse.Namespace) -> None:
    depth_ensemble = None
    if args.depth_ensemble:
        depth_ensemble = [s.strip() for s in str(args.depth_ensemble).split(",") if s.strip()]

    def _read_list(path: Path) -> list[str]:
        items: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            items.append(line)
        return items

    base = parse_filter_file(args.filter_file) if args.filter_file else ImageSelectionSpec()
    include_names = base.include_names + (_read_list(args.include_file) if args.include_file else [])
    exclude_names = base.exclude_names + (_read_list(args.exclude_file) if args.exclude_file else [])
    include_indices = list(base.include_indices)
    exclude_indices = list(base.exclude_indices)
    if args.include_indices:
        include_indices.append(str(args.include_indices))
    if args.exclude_indices:
        exclude_indices.append(str(args.exclude_indices))
    sel = ImageSelectionSpec(
        include_globs=base.include_globs + list(args.include_glob or []),
        exclude_globs=base.exclude_globs + list(args.exclude_glob or []),
        include_indices=include_indices,
        exclude_indices=exclude_indices,
        include_names=include_names,
        exclude_names=exclude_names,
    )
    if not (sel.has_includes() or sel.exclude_globs or sel.exclude_indices or sel.exclude_names):
        sel = None

    result = run_listing(
        images_dir=args.images,
        listing_id=args.listing_id,
        label_sqft=args.label_sqft,
        image_selection=sel,
        max_side=args.max_side,
        use_colmap=args.colmap,
        sfm_matching=args.sfm_matching,
        pair_embed=args.pair_embed,
        pair_topk=args.pair_topk,
        pair_min_sim=args.pair_min_sim,
        multi_component=args.multi_component,
        depth_model=args.depth_model,
        depth_encoder=args.depth_encoder,
        depth_dataset=args.depth_dataset,
        depth_input_size=args.depth_input_size,
        depth_ensemble=depth_ensemble,
        max_depth_m=args.max_depth_m,
        pc_stride=args.pc_stride,
        alpha=args.alpha,
        fusion=args.fusion,
        uncertainty=args.uncertainty,
        mc_samples=args.mc_samples,
        fallback=args.fallback,
        out_json=args.out_json,
    )
    print(json.dumps(result, indent=2))


def _cmd_eval(args: argparse.Namespace) -> None:
    if not args.dataset:
        raise SystemExit("No dataset provided and no default dataset found.")
    listing_ids = None
    if args.listing_ids:
        listing_ids = [s.strip() for s in str(args.listing_ids).split(",") if s.strip()]
    result = run_streeteasy_eval(
        dataset_path=args.dataset,
        downloads_dir=args.downloads,
        limit=args.limit,
        has_sqft=bool(args.has_sqft),
        listing_ids=listing_ids,
        filters_dir=args.filters_dir,
        out_json=args.out_json,
    )
    print(json.dumps(result, indent=2))


def _cmd_sweep(args: argparse.Namespace) -> None:
    if not args.dataset:
        raise SystemExit("No dataset provided and no default dataset found.")
    listing_ids = None
    if args.listing_ids:
        listing_ids = [s.strip() for s in str(args.listing_ids).split(",") if s.strip()]
    result = run_streeteasy_sweep(
        dataset_path=args.dataset,
        downloads_dir=args.downloads,
        config_path=args.config,
        limit=args.limit,
        has_sqft=bool(args.has_sqft),
        listing_ids=listing_ids,
        filters_dir=args.filters_dir,
        out_json=args.out_json,
    )
    print(json.dumps(result, indent=2))


def _cmd_curate(args: argparse.Namespace) -> None:
    if not args.dataset:
        raise SystemExit("No dataset provided and no default dataset found.")
    listing_ids = None
    if args.listing_ids:
        listing_ids = [s.strip() for s in str(args.listing_ids).split(",") if s.strip()]
    result = curate_streeteasy(
        dataset_path=args.dataset,
        downloads_dir=args.downloads,
        out_dir=args.out_dir,
        limit=args.limit,
        has_sqft=bool(args.has_sqft),
        listing_ids=listing_ids,
        overwrite=bool(args.overwrite),
        cols=args.cols,
        thumb_size=args.thumb_size,
        max_images=args.max_images,
    )
    print(json.dumps(result, indent=2))


def _cmd_calibrate(args: argparse.Namespace) -> None:
    data = json.loads(args.eval_json.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    y = []
    lo = []
    hi = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not isinstance(r.get("label_sqft"), (int, float)):
            continue
        interval = r.get("interval_90")
        if not (isinstance(interval, list) and len(interval) == 2):
            continue
        if not isinstance(interval[0], (int, float)) or not isinstance(interval[1], (int, float)):
            continue
        y.append(float(r["label_sqft"]))
        lo.append(float(interval[0]))
        hi.append(float(interval[1]))

    if not y:
        raise SystemExit("No labeled rows with interval_90 found in eval JSON.")

    cal = fit_conformal_interval_calibrator(
        y_true=np.asarray(y, dtype=np.float64),
        lo=np.asarray(lo, dtype=np.float64),
        hi=np.asarray(hi, dtype=np.float64),
        alpha=float(args.alpha),
    )
    out = {"alpha": cal.alpha, "q": cal.q, "n": len(y), "source_eval_json": str(args.eval_json)}
    out_path = args.out_json or (args.eval_json.parent / "calibration_conformal.json")
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


def _cmd_report_eval(args: argparse.Namespace) -> None:
    data = json.loads(args.eval_json.read_text(encoding="utf-8"))
    cal = load_conformal_calibrator(args.calibration_json) if args.calibration_json else None

    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        rows = data.get("rows", [])
        summary = summarize_eval_rows(
            rows,
            interval_key="interval_90",
            pred_key="pred_sqft",
            label_key="label_sqft",
            calibrator=cal,
        ).to_dict()
        out = {"source": str(args.eval_json), "calibration": str(args.calibration_json) if args.calibration_json else None, "summary": summary}
    elif isinstance(data, dict) and isinstance(data.get("runs"), list):
        runs = []
        for r in data.get("runs", []):
            if not isinstance(r, dict) or not isinstance(r.get("rows"), list):
                continue
            summ = summarize_eval_rows(
                r["rows"],
                interval_key="interval_90",
                pred_key="pred_sqft",
                label_key="label_sqft",
                calibrator=cal,
            ).to_dict()
            runs.append({"cfg_id": r.get("cfg_id"), "metrics": summ, "run_cfg": r.get("run_cfg")})
        runs_sorted = sorted(
            runs,
            key=lambda x: (float("inf") if x["metrics"].get("mae_sqft") is None else float(x["metrics"]["mae_sqft"])),
        )
        out = {
            "source": str(args.eval_json),
            "calibration": str(args.calibration_json) if args.calibration_json else None,
            "n_runs": len(runs_sorted),
            "runs_by_mae": runs_sorted,
            "best": runs_sorted[0] if runs_sorted else None,
        }
    else:
        raise SystemExit("Unsupported JSON format (expected eval-streeteasy or sweep-streeteasy output).")

    out_path = args.out_json or (args.eval_json.parent / "report_eval.json")
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)
