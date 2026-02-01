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
    Matches sample-collection/scripts/download_photos.py (_safe_dirname).
    """
    path = urlparse(listing_url).path.strip("/")
    path = re.sub(r"[^a-zA-Z0-9._/-]+", "_", path)
    return path.replace("/", "__") or "listing"


def _load_dataset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_streeteasy_dataset(dataset_path: Path, downloads_dir: Path) -> list[StreetEasyExample]:
    dataset = _load_dataset(dataset_path)
    examples: list[StreetEasyExample] = []
    for ex in dataset["examples"]:
        listing_url = ex["listingUrl"]
        listing_id = safe_dirname(listing_url)
        sqft = ex.get("sqft", None)
        images_dir = downloads_dir / listing_id
        examples.append(
            StreetEasyExample(listing_url=listing_url, listing_id=listing_id, sqft=sqft, images_dir=images_dir)
        )
    return examples

