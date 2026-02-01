#!/usr/bin/env python3

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


DATA_DIR = Path(__file__).parent.parent / "data"


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload:
        payload = _load_json(args.payload)
    else:
        payload = json.load(sys.stdin)

    if not isinstance(payload, dict):
        raise SystemExit("Payload must be a JSON object.")

    if not payload.get("listingUrl"):
        raise SystemExit("Payload missing required field: listingUrl")

    if "photoIds" not in payload or not isinstance(payload["photoIds"], list):
        raise SystemExit("Payload missing required field: photoIds (array)")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append a single extracted listing payload into the dataset JSON."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATA_DIR / "streeteasy_examples_20.json",
    )
    parser.add_argument(
        "--payload",
        type=Path,
        help="Path to a JSON payload (if omitted, read JSON from stdin).",
    )
    parser.add_argument("--replace", action="store_true", help="Replace if listingUrl exists.")
    args = parser.parse_args()

    dataset = _load_json(args.dataset)
    payload = _load_payload(args)

    max_ids = int(dataset.get("maxPhotoIdsPerListing", 30))
    payload["photoIds"] = payload["photoIds"][:max_ids]

    examples = dataset.get("examples")
    if not isinstance(examples, list):
        raise SystemExit("Dataset file missing 'examples' array.")

    listing_url = payload["listingUrl"]
    existing_index = next((i for i, ex in enumerate(examples) if ex.get("listingUrl") == listing_url), None)
    if existing_index is not None:
        if args.replace:
            examples[existing_index] = payload
        else:
            print(json.dumps({"ok": False, "error": "duplicate", "listingUrl": listing_url}))
            return
    else:
        examples.append(payload)

    dataset["collectedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    args.dataset.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "listingUrl": listing_url}, ensure_ascii=False))


if __name__ == "__main__":
    main()
