#!/usr/bin/env python3
"""
Extract listing data from StreetEasy listing URLs.
Visits each URL and extracts: title, sqft, photo IDs.
"""

import re
import time
import random
import json
from pathlib import Path
from datetime import datetime, timezone

import httpx

DATA_DIR = Path(__file__).parent.parent / "data"
URLS_FILE = DATA_DIR / "listing_urls.txt"
DATASET_FILE = DATA_DIR / "streeteasy_examples_20.json"
PROGRESS_FILE = DATA_DIR / "extraction_progress.json"

# Browser-like headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def extract_listing_data(html: str, url: str) -> dict | None:
    """Extract listing data from page HTML."""
    # Extract title
    title_match = re.search(r"<title>([^<]+)</title>", html)
    title = title_match.group(1) if title_match else ""

    # Extract sqft - look for pattern like "750 ft²" or "1,200 ft²"
    sqft_match = re.search(r"(\d[\d,]*)\s*ft²", html)
    sqft = int(sqft_match.group(1).replace(",", "")) if sqft_match else None
    sqft_text = sqft_match.group(0) if sqft_match else ("- ft²" if "- ft²" in html else None)

    # Extract photo IDs from zillowstatic URLs
    photo_pattern = r"photos\.zillowstatic\.com/fp/([a-f0-9]{32})"
    photo_ids = list(set(re.findall(photo_pattern, html.lower())))[:30]

    if not photo_ids:
        return None  # Skip listings without photos

    return {
        "listingUrl": url.split("?")[0],
        "title": title,
        "sqft": sqft,
        "sqftText": sqft_text,
        "photoIdCountDetected": len(photo_ids),
        "photoIdCountUsed": len(photo_ids),
        "photoIds": photo_ids,
    }


def load_dataset() -> dict:
    """Load existing dataset."""
    if DATASET_FILE.exists():
        return json.loads(DATASET_FILE.read_text())
    return {
        "source": "streeteasy",
        "collectedAt": datetime.now(timezone.utc).isoformat(),
        "photoUrlTemplate": "https://photos.zillowstatic.com/fp/{id}-full.jpg",
        "maxPhotoIdsPerListing": 30,
        "examples": [],
    }


def save_dataset(dataset: dict):
    """Save dataset to file."""
    dataset["collectedAt"] = datetime.now(timezone.utc).isoformat()
    DATASET_FILE.write_text(json.dumps(dataset, indent=2))


def load_progress() -> dict:
    """Load extraction progress."""
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"processed": [], "failed": []}


def save_progress(progress: dict):
    """Save extraction progress."""
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def main():
    # Load URLs
    if not URLS_FILE.exists():
        print(f"URLs file not found: {URLS_FILE}")
        print("Run collect_listing_urls.py first.")
        return

    urls = [line.strip() for line in URLS_FILE.read_text().strip().split("\n") if line.strip()]
    print(f"Loaded {len(urls)} URLs")

    # Load existing data
    dataset = load_dataset()
    progress = load_progress()

    existing_urls = {ex["listingUrl"] for ex in dataset["examples"]}
    print(f"Existing examples: {len(dataset['examples'])}")

    # Filter out already processed URLs
    urls_to_process = [
        url for url in urls
        if url.split("?")[0] not in existing_urls
        and url not in progress["processed"]
        and url not in progress["failed"]
    ]
    print(f"URLs to process: {len(urls_to_process)}")

    if not urls_to_process:
        print("All URLs already processed!")
        return

    # Target: collect until we have 200+ examples
    target = 200
    current = len(dataset["examples"])

    with_sqft = 0
    without_sqft = 0
    blocked = 0

    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        for i, url in enumerate(urls_to_process):
            if current >= target:
                print(f"\nReached target of {target} examples!")
                break

            print(f"[{current}/{target}] Fetching: {url.split('/building/')[1][:40]}... ", end="", flush=True)

            try:
                response = client.get(url)

                if response.status_code == 403:
                    blocked += 1
                    print("BLOCKED")
                    progress["failed"].append(url)
                    if blocked >= 3:
                        print("\nToo many blocks. Try using browser automation.")
                        break
                    continue
                elif response.status_code != 200:
                    print(f"ERROR ({response.status_code})")
                    progress["failed"].append(url)
                    continue

                data = extract_listing_data(response.text, url)

                if data:
                    dataset["examples"].append(data)
                    progress["processed"].append(url)
                    current += 1

                    if data["sqft"]:
                        with_sqft += 1
                        print(f"OK ({data['sqft']} ft², {len(data['photoIds'])} photos)")
                    else:
                        without_sqft += 1
                        print(f"OK (no sqft, {len(data['photoIds'])} photos)")

                    # Save periodically
                    if current % 10 == 0:
                        save_dataset(dataset)
                        save_progress(progress)
                else:
                    print("SKIP (no photos)")
                    progress["failed"].append(url)

                # Random delay
                delay = random.uniform(1.5, 4)
                time.sleep(delay)

            except Exception as e:
                print(f"ERROR: {e}")
                progress["failed"].append(url)
                continue

    # Final save
    save_dataset(dataset)
    save_progress(progress)

    print()
    print("=" * 50)
    print(f"Total examples: {len(dataset['examples'])}")
    print(f"  With sqft: {with_sqft}")
    print(f"  Without sqft: {without_sqft}")
    print(f"Blocked requests: {blocked}")
    print(f"Dataset saved to: {DATASET_FILE}")


if __name__ == "__main__":
    main()
