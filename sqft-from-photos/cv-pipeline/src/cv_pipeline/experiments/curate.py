from __future__ import annotations

import json
from pathlib import Path

from cv_pipeline.dataset import load_streeteasy_dataset
from cv_pipeline.image.curation import build_image_manifest, write_contact_sheet
from cv_pipeline.image.preprocess import list_images
from cv_pipeline.paths import VolumePaths, default_volume_root
from cv_pipeline.utils.ids import new_run_id


def curate_streeteasy(
    *,
    dataset_path: Path,
    downloads_dir: Path | None,
    out_dir: Path | None,
    limit: int,
    has_sqft: bool = False,
    listing_ids: list[str] | None = None,
    overwrite: bool = False,
    cols: int = 6,
    thumb_size: int = 256,
    max_images: int = 120,
) -> dict[str, object]:
    examples = load_streeteasy_dataset(dataset_path, downloads_dir)
    if has_sqft:
        examples = [ex for ex in examples if isinstance(ex.sqft, (int, float))]
    if listing_ids:
        wanted = [str(s).strip() for s in listing_ids if str(s).strip()]
        ex_by_id = {ex.listing_id: ex for ex in examples}
        examples = [ex_by_id[i] for i in wanted if i in ex_by_id]
    if limit and limit > 0:
        examples = examples[:limit]

    volume = VolumePaths(root=default_volume_root())
    if out_dir is None:
        out_dir = volume.root / "curation" / new_run_id(prefix="curation")
    out_dir.mkdir(parents=True, exist_ok=True)

    filters_dir = out_dir / "filters"
    listings_dir = out_dir / "listings"
    filters_dir.mkdir(parents=True, exist_ok=True)
    listings_dir.mkdir(parents=True, exist_ok=True)

    selected = []
    for ex in examples:
        images_dir = ex.images_dir
        listing_id = ex.listing_id
        listing_out = listings_dir / listing_id
        listing_out.mkdir(parents=True, exist_ok=True)

        if not images_dir.exists():
            selected.append(
                {
                    "listing_id": listing_id,
                    "listing_url": ex.listing_url,
                    "label_sqft": ex.sqft,
                    "images_dir": str(images_dir),
                    "error": "missing images_dir",
                }
            )
            continue

        image_paths = list_images(images_dir)
        manifest = build_image_manifest(images_dir, image_paths)

        manifest_path = listing_out / "manifest.json"
        if overwrite or not manifest_path.exists():
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        cs = write_contact_sheet(
            image_paths,
            out_path=listing_out / "contact_sheet.jpg",
            cols=cols,
            thumb_size=thumb_size,
            max_images=max_images,
            title=f"{listing_id} ({len(image_paths)} images)",
        )

        filter_file = filters_dir / f"{listing_id}.txt"
        if overwrite or not filter_file.exists():
            filter_file.write_text(
                "\n".join(
                    [
                        f"# Photo filter for: {listing_id}",
                        f"# Images dir: {images_dir}",
                        f"# URL: {ex.listing_url}",
                        f"# Ground truth sqft: {ex.sqft}",
                        "#",
                        "# Indices refer to the order in:",
                        f"#   {manifest_path}",
                        "#",
                        "# Examples:",
                        "#   include_index: 0-12",
                        "#   exclude_index: 7,8",
                        "#   exclude_glob: **/*exterior*",
                        "#",
                        "# Empty file => include all images.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        selected.append(
            {
                "listing_id": listing_id,
                "listing_url": ex.listing_url,
                "label_sqft": ex.sqft,
                "images_dir": str(images_dir),
                "out_dir": str(listing_out),
                "manifest": str(manifest_path),
                "contact_sheet": cs.get("out_path"),
                "filter_file": str(filter_file),
            }
        )

    meta = {
        "dataset": str(dataset_path),
        "downloads": str(downloads_dir),
        "filters_dir": str(filters_dir),
        "out_dir": str(out_dir),
        "n_listings": len(selected),
        "has_sqft": bool(has_sqft),
        "listing_ids": listing_ids,
        "listings": selected,
    }
    (out_dir / "curation.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
