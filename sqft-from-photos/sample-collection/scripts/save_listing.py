#!/usr/bin/env python3
"""Save extracted listing data to dataset."""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "data"
DATASET_FILE = DATA_DIR / "streeteasy_examples_20.json"

def save_listing(url, title, has_dash_ft, photo_chunks):
    """Save a listing to the dataset."""
    # Load dataset
    dataset = json.loads(DATASET_FILE.read_text())

    # Check if already exists
    if url in {ex["listingUrl"] for ex in dataset["examples"]}:
        print(f"Listing already exists: {url}")
        return False

    # Process photo IDs
    photo_ids = ["".join(chunks) for chunks in photo_chunks]
    # Remove duplicates while preserving order
    seen = set()
    unique_photo_ids = []
    for pid in photo_ids:
        if pid not in seen:
            seen.add(pid)
            unique_photo_ids.append(pid)

    # Create listing entry
    listing = {
        "listingUrl": url,
        "title": title,
        "sqft": None if has_dash_ft else None,  # Will be filled if we find sqft
        "sqftText": "- ft²" if has_dash_ft else None,
        "photoIdCountDetected": len(unique_photo_ids),
        "photoIdCountUsed": min(len(unique_photo_ids), 30),
        "photoIds": unique_photo_ids[:30]
    }

    # Add to dataset
    dataset["examples"].append(listing)
    dataset["collectedAt"] = datetime.now(timezone.utc).isoformat()

    # Save
    DATASET_FILE.write_text(json.dumps(dataset, indent=2))

    print(f"Saved listing: {url}")
    print(f"  Total examples: {len(dataset['examples'])}")
    print(f"  Photos: {len(unique_photo_ids)}")
    return True

if __name__ == "__main__":
    # Example usage for the current listing
    url = "https://streeteasy.com/building/the-greystone/539"
    title = "212 West 91st Street #539 in Upper West Side, Manhattan | StreetEasy"
    has_dash_ft = True
    photo_chunks = [
        ["686a1dbd", "a76badf7", "ee17a369", "96bae5b3"],
        ["cfba5226", "90f25722", "29dcfe08", "8a055069"],
        ["8e14563a", "984fd705", "0a714eeb", "49938af3"],
        ["45a4c60f", "4999a389", "fb1a573a", "c8193366"],
        ["703cc294", "ee444e6c", "3f18110c", "983b4ccd"],
        ["47e94a23", "0c19a427", "88ddde8e", "3a25fbe6"],
        ["2913b514", "a21c3809", "fd88bfa0", "6ebd1d79"],
        ["7f237b0c", "235f6575", "cb1752ab", "9cad02c7"],
        ["d03be1ee", "045a0ca8", "513901e6", "93b5b324"],
        ["b168a7ce", "e8747ace", "83dbccfd", "9085ec3c"],
        ["257cc4fe", "664e6dba", "1fe0430f", "d9cb035d"],
        ["2d145843", "990a4168", "1e87b552", "7b80feab"],
        ["b6fb4bd1", "97890a33", "c33aa76d", "c4f84d28"],
        ["47b0cdfc", "b31e742c", "e0068abe", "8e485f8d"],
        ["bb022fad", "19d0c715", "5d0e2019", "bc14f238"],
        ["2e3e96b8", "25dc5254", "f76fa51c", "047e660c"],
        ["cdff793a", "e8e54bd6", "937b9590", "4d3c60e6"],
    ]

    save_listing(url, title, has_dash_ft, photo_chunks)
