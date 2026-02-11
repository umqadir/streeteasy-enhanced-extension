#!/usr/bin/env python3
"""Export the complete dataset with photos to a distributable archive."""

import json
import shutil
from pathlib import Path
from datetime import datetime

def create_export():
    """Create a complete export archive."""
    # Load dataset
    dataset_path = Path('data/streeteasy_examples_20.json')
    data = json.loads(dataset_path.read_text())

    # Create export directory
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    export_dir = Path(f'exports/streeteasy_dataset_{timestamp}')
    export_dir.mkdir(parents=True, exist_ok=True)

    print(f"Creating export: {export_dir}")
    print(f"Listings: {len(data['examples'])}")
    print(f"Photos: {data.get('totalPhotosDownloaded', 'unknown')}\n")

    # Copy dataset JSON
    shutil.copy(dataset_path, export_dir / 'dataset.json')
    print(f"✓ Copied dataset.json")

    # Copy photos directory
    photos_dir = Path('data/photos')
    if photos_dir.exists():
        shutil.copytree(photos_dir, export_dir / 'photos', dirs_exist_ok=True)
        print(f"✓ Copied photos directory")

    # Create README
    readme = f"""# StreetEasy Dataset Export

## Summary
- **Exported:** {datetime.now().isoformat()}
- **Listings:** {len(data['examples'])}/200
- **Photos:** {data.get('totalPhotosDownloaded', 'unknown')} downloaded
- **Collection Period:** {data.get('collectedAt', 'unknown')}

## Files
- `dataset.json` - Main dataset with all listing metadata
- `photos/` - Directory containing all downloaded photos organized by listing

## Dataset Structure
Each listing contains:
- `listingUrl` - Original StreetEasy URL
- `title` - Listing title
- `sqft` - Square footage (null for now)
- `sqftText` - "- ft²" if missing, null if present
- `photoIds` - Array of 32-character photo IDs
- `photoIdCountDetected` - Number of unique photos found
- `photoIdCountUsed` - Number of photos stored (max 30)
- `localPhotoPaths` - Array of local file paths to downloaded photos
- `photoDownloadCount` - Number of successfully downloaded photos

## Photo Organization
Photos are organized as: `photos/NNN_listing-id/NN_photo-id.jpg`
- `NNN` - 3-digit listing index (001, 002, etc.)
- `listing-id` - URL slug of the listing
- `NN` - 2-digit photo index within listing
- `photo-id` - 32-character Zillowstatic photo ID

## Usage
```python
import json
from pathlib import Path

# Load dataset
data = json.load(open('dataset.json'))

# Access listings
for listing in data['examples']:
    print(f"Title: {{listing['title']}}")
    print(f"URL: {{listing['listingUrl']}}")
    print(f"Photos: {{listing['photoDownloadCount']}}")

    # Access local photos
    for photo_path in listing.get('localPhotoPaths', []):
        # photo_path is relative to export directory
        print(f"  - {{photo_path}}")
```

## Next Steps
This dataset can be used for:
1. Computer vision training for square footage extraction
2. Real estate image analysis
3. StreetEasy browser extension enhancement
"""

    (export_dir / 'README.md').write_text(readme)
    print(f"✓ Created README.md")

    # Create archive
    archive_name = f'streeteasy_dataset_{timestamp}'
    print(f"\nCreating archive...")
    shutil.make_archive(f'exports/{archive_name}', 'zip', export_dir.parent, export_dir.name)
    print(f"✓ Created {archive_name}.zip")

    print(f"\n{'='*60}")
    print(f"Export complete!")
    print(f"  Directory: {export_dir.absolute()}")
    print(f"  Archive: {Path(f'exports/{archive_name}.zip').absolute()}")
    print(f"  Size: {sum(f.stat().st_size for f in export_dir.rglob('*') if f.is_file()) / 1024 / 1024:.1f} MB")

if __name__ == '__main__':
    Path('exports').mkdir(exist_ok=True)
    create_export()
