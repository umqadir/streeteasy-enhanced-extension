#!/usr/bin/env python3
"""Download all photos from collected StreetEasy listings."""

import json
import httpx
from pathlib import Path
from datetime import datetime

def download_photos_for_listing(listing_index, listing_data, photos_dir):
    """Download all photos for a single listing."""
    listing_id = listing_data['listingUrl'].split('/')[-1]
    listing_dir = photos_dir / f"{listing_index:03d}_{listing_id}"
    listing_dir.mkdir(exist_ok=True)

    downloaded = []
    failed = []

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
            else:
                failed.append(photo_id)
        except Exception as e:
            print(f"  Failed to download {photo_id}: {e}")
            failed.append(photo_id)

    return downloaded, failed

def main():
    # Load dataset
    dataset_path = Path('data/streeteasy_examples_20.json')
    data = json.loads(dataset_path.read_text())

    photos_dir = Path('data/photos')
    photos_dir.mkdir(exist_ok=True)

    print(f"Downloading photos for {len(data['examples'])} listings...")
    print(f"Photos will be saved to: {photos_dir.absolute()}\n")

    total_downloaded = 0
    total_failed = 0

    # Download photos for each listing
    for idx, listing in enumerate(data['examples']):
        print(f"[{idx+1}/{len(data['examples'])}] {listing['title'][:60]}...")

        downloaded, failed = download_photos_for_listing(idx + 1, listing, photos_dir)

        # Update listing with photo paths
        listing['localPhotoPaths'] = downloaded
        listing['photoDownloadFailed'] = failed
        listing['photoDownloadCount'] = len(downloaded)

        total_downloaded += len(downloaded)
        total_failed += len(failed)

        print(f"  ✓ Downloaded: {len(downloaded)}, Failed: {len(failed)}")

    # Update dataset with download info
    data['photosDownloadedAt'] = datetime.utcnow().isoformat()
    data['totalPhotosDownloaded'] = total_downloaded
    data['totalPhotosFailed'] = total_failed

    dataset_path.write_text(json.dumps(data, indent=2))

    print(f"\n{'='*60}")
    print(f"✓ Complete!")
    print(f"  Total photos downloaded: {total_downloaded}")
    print(f"  Total photos failed: {total_failed}")
    print(f"  Dataset updated: {dataset_path}")
    print(f"  Photos directory: {photos_dir.absolute()}")

if __name__ == '__main__':
    main()
