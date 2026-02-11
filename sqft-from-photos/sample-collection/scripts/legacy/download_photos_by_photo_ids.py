#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


def _safe_dirname(listing_url: str) -> str:
    path = urlparse(listing_url).path.strip("/")
    path = re.sub(r"[^a-zA-Z0-9._/-]+", "_", path)
    return path.replace("/", "__") or "listing"


def _load_dataset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


DATA_DIR = Path(__file__).parent.parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download StreetEasy photo URLs (zillowstatic) by ID.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATA_DIR / "streeteasy_examples_20.json",
    )
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "downloads")
    parser.add_argument("--max-per-listing", type=int, default=3)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    template = dataset["photoUrlTemplate"]
    examples = dataset["examples"]

    args.out_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for example in examples:
            listing_url = example["listingUrl"]
            listing_dir = args.out_dir / _safe_dirname(listing_url)
            listing_dir.mkdir(parents=True, exist_ok=True)

            for i, photo_id in enumerate(example["photoIds"][: args.max_per_listing], start=1):
                url = template.format(id=photo_id)
                out_path = listing_dir / f"{i:02d}_{photo_id}.jpg"

                if args.dry_run:
                    print(json.dumps({"url": url, "out": str(out_path)}, ensure_ascii=False))
                    continue

                if out_path.exists() and not args.overwrite:
                    continue

                r = client.get(url)
                r.raise_for_status()
                out_path.write_bytes(r.content)


if __name__ == "__main__":
    main()
