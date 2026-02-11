from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class StreetEasyExample:
    listing_url: str
    listing_id: str
    sqft: float | None
    images_dir: Path


def safe_dirname(listing_url: str) -> str:
    """
    Matches sample-collection/scripts/legacy/download_photos_by_photo_ids.py (_safe_dirname).
    """
    path = urlparse(listing_url).path.strip("/")
    path = re.sub(r"[^a-zA-Z0-9._/-]+", "_", path)
    return path.replace("/", "__") or "listing"


def _load_dataset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_streeteasy_dataset(dataset_path: Path, downloads_dir: Path | None = None) -> list[StreetEasyExample]:
    dataset = _load_dataset(dataset_path)
    examples: list[StreetEasyExample] = []

    # Format A (legacy): sample-collection/data/streeteasy_examples_20.json
    # {
    #   "examples": [{"listingUrl": "...", "sqft": 1234, ...}, ...],
    #   ...
    # }
    if "examples" in dataset:
        if downloads_dir is None:
            # Default co-located downloads folder.
            downloads_dir = dataset_path.parent / "downloads"
        for ex in dataset["examples"]:
            listing_url = ex["listingUrl"]
            listing_id = safe_dirname(listing_url)
            sqft = ex.get("sqft", None)
            images_dir = downloads_dir / listing_id
            examples.append(
                StreetEasyExample(listing_url=listing_url, listing_id=listing_id, sqft=sqft, images_dir=images_dir)
            )
        return examples

    # Format B (eval dataset): sample-collection/streeteasy_eval_dataset/listings.json
    # {
    #   "dataset_info": {...},
    #   "listings": [{"id": "listing_001", "url": "...", "has_sqft_data": false, "photo_paths": [...]}]
    # }
    if "listings" in dataset:
        dataset_root = dataset_path.parent
        for ex in dataset["listings"]:
            listing_id = str(ex.get("id", "")).strip()
            if not listing_id:
                continue
            listing_url = str(ex.get("url", "")).strip()
            sqft = ex.get("sqft", None)
            if not isinstance(sqft, (int, float)):
                sqft = None
            # Photos are organized as: <dataset_root>/photos/<listing_id>/...
            images_dir = dataset_root / "photos" / listing_id
            examples.append(
                StreetEasyExample(listing_url=listing_url, listing_id=listing_id, sqft=sqft, images_dir=images_dir)
            )
        return examples

    raise ValueError(
        f"Unsupported StreetEasy dataset format at {dataset_path} "
        "(expected keys: 'examples' or 'listings')."
    )
