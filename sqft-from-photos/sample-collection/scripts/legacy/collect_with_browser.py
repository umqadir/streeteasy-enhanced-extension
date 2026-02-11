#!/usr/bin/env python3
"""Collect StreetEasy listings using browser automation."""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

# Paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATASET_FILE = DATA_DIR / "streeteasy_examples_20.json"
LISTING_URLS_FILE = DATA_DIR / "listing_urls.txt"

def load_dataset():
    """Load the current dataset."""
    return json.loads(DATASET_FILE.read_text())

def save_dataset(dataset):
    """Save the dataset."""
    dataset["collectedAt"] = datetime.now(timezone.utc).isoformat()
    DATASET_FILE.write_text(json.dumps(dataset, indent=2))

def get_urls_to_process():
    """Get URLs that haven't been processed yet."""
    dataset = load_dataset()
    existing_urls = {ex["listingUrl"] for ex in dataset["examples"]}
    all_urls = LISTING_URLS_FILE.read_text().strip().split("\n")
    return [url for url in all_urls if url not in existing_urls]

def save_listing(url, title, has_dash_ft, photo_chunks):
    """Save a listing to the dataset."""
    dataset = load_dataset()
    
    if url in {ex["listingUrl"] for ex in dataset["examples"]}:
        return False
    
    photo_ids = ["".join(chunks) for chunks in photo_chunks]
    seen = set()
    unique_photo_ids = []
    for pid in photo_ids:
        if pid not in seen:
            seen.add(pid)
            unique_photo_ids.append(pid)
    
    listing = {
        "listingUrl": url,
        "title": title,
        "sqft": None,
        "sqftText": "- ft²" if has_dash_ft else None,
        "photoIdCountDetected": len(unique_photo_ids),
        "photoIdCountUsed": min(len(unique_photo_ids), 30),
        "photoIds": unique_photo_ids[:30]
    }
    
    dataset["examples"].append(listing)
    save_dataset(dataset)
    return len(dataset["examples"])

print("Helper functions loaded. Use save_listing() to save extracted data.")
