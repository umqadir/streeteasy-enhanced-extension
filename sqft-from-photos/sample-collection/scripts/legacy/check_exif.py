#!/usr/bin/env python3

import argparse
import io
import json
from pathlib import Path
from typing import Any

import httpx
from PIL import Image


def _load_dataset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _has_exif(image_bytes: bytes) -> bool:
    img = Image.open(io.BytesIO(image_bytes))
    exif = img.getexif()
    return bool(exif and len(exif) > 0)


DATA_DIR = Path(__file__).parent.parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check EXIF presence in downloaded listing photos.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATA_DIR / "streeteasy_examples_20.json",
    )
    parser.add_argument("--max-per-listing", type=int, default=2)
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    template = dataset["photoUrlTemplate"]
    examples = dataset["examples"]

    checked = 0
    with_exif = 0
    per_listing = []

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for example in examples:
            listing_url = example["listingUrl"]
            hit = 0
            total = 0
            for photo_id in example["photoIds"][: args.max_per_listing]:
                url = template.format(id=photo_id)
                r = client.get(url)
                r.raise_for_status()
                total += 1
                checked += 1
                if _has_exif(r.content):
                    hit += 1
                    with_exif += 1

            per_listing.append({"listingUrl": listing_url, "checked": total, "withExif": hit})

    print(json.dumps({"checked": checked, "withExif": with_exif, "perListing": per_listing}, ensure_ascii=False))


if __name__ == "__main__":
    main()
