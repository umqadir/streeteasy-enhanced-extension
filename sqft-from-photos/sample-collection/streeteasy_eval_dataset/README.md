# StreetEasy Evaluation Dataset

## Overview
Dataset of 138 StreetEasy rental listings with photos for evaluating square footage extraction from images.

## Statistics
- **Listings:** 138
- **Photos:** 3116
- **Listings with sqft data:** 21
- **Listings without sqft data:** 117

## Structure
```
streeteasy_eval_dataset/
├── listings.json          # All listing metadata
├── photos/               # All photos organized by listing
│   ├── listing_001/
│   │   ├── photo_00.jpg
│   │   ├── photo_01.jpg
│   │   └── ...
│   ├── listing_002/
│   └── ...
└── README.md            # This file
```

## Usage

```python
import json

# Load dataset
data = json.load(open('listings.json'))

# Access listings
for listing in data['listings']:
    print(f"ID: {listing['id']}")
    print(f"Title: {listing['title']}")
    print(f"Has SqFt: {listing['has_sqft_data']}")
    print(f"Photos: {listing['photo_count']}")
    
    # Load photos for CV model
    for photo_path in listing['photo_paths']:
        # photo_path is relative to this directory
        # e.g., "photos/listing_001/photo_00.jpg"
        pass
```

## Listing Fields

- `id`: Unique identifier (listing_001, listing_002, etc.)
- `listing_number`: Sequential number (1-138)
- `title`: Full listing title from StreetEasy
- `url`: Original StreetEasy URL
- `has_sqft_data`: Boolean - true if listing has actual sqft, false if shows "- ft²"
- `photo_count`: Number of photos for this listing
- `photo_paths`: Array of relative paths to photos

## Ground Truth Labels

The `has_sqft_data` field indicates whether the listing originally showed square footage:
- `true`: StreetEasy displayed actual sqft (ground truth available on site)
- `false`: StreetEasy showed "- ft²" (no ground truth, model must extract)

This can be used to validate model extractions against known values.
