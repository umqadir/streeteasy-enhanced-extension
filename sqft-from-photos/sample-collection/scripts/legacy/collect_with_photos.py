#!/usr/bin/env python3
"""
Collect StreetEasy listings with browser automation AND download photos.
This script is designed to be called after each listing is extracted.
"""

import json
import httpx
import sys
from pathlib import Path
from datetime import datetime, timezone

def download_photos_for_listing(listing_index, listing_data, photos_dir):
    """Download all photos for a single listing."""
    listing_id = listing_data['listingUrl'].split('/')[-1]
    listing_dir = photos_dir / f"{listing_index:03d}_{listing_id}"
    listing_dir.mkdir(exist_ok=True, parents=True)

    downloaded = []

    for i, photo_id in enumerate(listing_data['photoIds']):
        photo_path = listing_dir / f"{i:02d}_{photo_id}.jpg"

        # Skip if already downloaded
        if photo_path.exists():
            downloaded.append(str(photo_path.relative_to(Path.cwd())))
            continue

        # Download photo
        url = f"https://photos.zillowstatic.com/fp/{photo_id}-p_e.jpg"
        try:
            response = httpx.get(url, timeout=10, follow_redirects=True)
            if response.status_code == 200:
                photo_path.write_bytes(response.content)
                downloaded.append(str(photo_path.relative_to(Path.cwd())))
        except Exception:
            pass  # Silently skip failed downloads

    return downloaded

def add_listing_with_photos(url, title, photo_chunks, has_dash_ft):
    """Add a listing to the dataset and download its photos."""
    dataset_path = Path('data/streeteasy_examples_20.json')
    data = json.loads(dataset_path.read_text())

    # Check if listing already exists
    if url in {ex['listingUrl'] for ex in data['examples']}:
        print('exists')
        return

    # Reconstruct photo IDs and deduplicate
    photo_ids = [''.join(chunk) for chunk in photo_chunks]
    seen = set()
    photo_ids = [x for x in photo_ids if not (x in seen or seen.add(x))]

    # Create listing entry
    listing = {
        'listingUrl': url,
        'title': title,
        'sqft': None,
        'sqftText': '- ft²' if has_dash_ft else None,
        'photoIdCountDetected': len(photo_ids),
        'photoIdCountUsed': min(len(photo_ids), 30),
        'photoIds': photo_ids[:30]
    }

    # Download photos
    photos_dir = Path('data/photos')
    listing_index = len(data['examples']) + 1
    downloaded = download_photos_for_listing(listing_index, listing, photos_dir)

    # Add download info to listing
    listing['localPhotoPaths'] = downloaded
    listing['photoDownloadCount'] = len(downloaded)

    # Add to dataset
    data['examples'].append(listing)
    data['collectedAt'] = datetime.now(timezone.utc).isoformat()

    # Save dataset
    dataset_path.write_text(json.dumps(data, indent=2))

    print(f"{len(data['examples'])}/200 ({len(downloaded)} photos)")

if __name__ == '__main__':
    # Usage: python collect_with_photos.py URL TITLE PHOTO_CHUNKS HAS_DASH_FT
    # This is called from the collection loop
    if len(sys.argv) >= 4:
        import ast
        url = sys.argv[1]
        title = sys.argv[2]
        photo_chunks = ast.literal_eval(sys.argv[3])
        has_dash_ft = sys.argv[4].lower() == 'true' if len(sys.argv) > 4 else False
        add_listing_with_photos(url, title, photo_chunks, has_dash_ft)
