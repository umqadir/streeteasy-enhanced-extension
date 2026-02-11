#!/usr/bin/env python3
"""Quick progress check for photo downloads and collection."""

import json
from pathlib import Path
from datetime import datetime

def check_progress():
    # Load dataset
    dataset_path = Path('data/streeteasy_examples_20.json')
    data = json.loads(dataset_path.read_text())

    total_listings = len(data['examples'])

    # Count downloaded photos
    photos_dir = Path('data/photos')
    if photos_dir.exists():
        listing_dirs = list(photos_dir.iterdir())
        photo_files = list(photos_dir.rglob('*.jpg'))
        total_size = sum(f.stat().st_size for f in photo_files) / 1024 / 1024

        # Get most recent directory
        if listing_dirs:
            latest = max(listing_dirs, key=lambda p: p.stat().st_mtime)
            latest_time = datetime.fromtimestamp(latest.stat().st_mtime).strftime('%H:%M:%S')
        else:
            latest = None
            latest_time = "N/A"
    else:
        listing_dirs = []
        photo_files = []
        total_size = 0
        latest = None
        latest_time = "N/A"

    # Calculate progress
    dirs_count = len(listing_dirs)
    photos_count = len(photo_files)
    progress_pct = (dirs_count / total_listings * 100) if total_listings > 0 else 0

    # Check if dataset has download info
    with_paths = sum(1 for ex in data['examples'] if 'localPhotoPaths' in ex)

    print(f"{'='*60}")
    print(f"📊 COLLECTION STATUS - {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    print(f"Listings collected:  {total_listings}/200 ({total_listings/2:.0f}%)")
    print(f"Photos downloaded:   {photos_count} in {dirs_count} directories")
    print(f"Download progress:   {dirs_count}/{total_listings} ({progress_pct:.0f}%)")
    print(f"Dataset updated:     {with_paths}/{total_listings} listings have photo paths")
    print(f"Disk usage:          {total_size:.1f} MB")
    print(f"Latest activity:     {latest_time} ({latest.name if latest else 'N/A'})")
    print(f"{'='*60}")

    if dirs_count < total_listings:
        remaining = total_listings - dirs_count
        print(f"⏳ Still downloading... {remaining} listings remaining")
    elif with_paths < total_listings:
        print(f"⚠️  Download complete but dataset not fully updated")
    else:
        print(f"✅ All photos downloaded and dataset updated!")

    print(f"{'='*60}")

if __name__ == '__main__':
    check_progress()
