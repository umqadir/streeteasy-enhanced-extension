# sqft-from-photos

Collect a small training set of StreetEasy listing photos + square footage labels for a future “estimate sqft from photos” model.

## What works (and what doesn’t)

- `curl`/headless scraping of `streeteasy.com` is frequently blocked (PerimeterX / 403).
- Photo assets are served from `photos.zillowstatic.com` and can be downloaded directly once you have the photo IDs.
- Most reliable path: use an interactive browser session (Playwright MCP or a normal browser) to extract photo IDs + sqft, then download photos from `photos.zillowstatic.com`.

If you hit a “Press & Hold” / “Access denied” page, switching to a fresh browser profile (or incognito) is usually the quickest fix.

## Dataset

- `sqft-from-photos/data/streeteasy_examples_20.json`
  - `sqft` is either an integer or `null` if StreetEasy shows `- ft²` for that listing.
  - `photoIds` are Zillowstatic IDs; build URLs via `photoUrlTemplate` (defaults to `.../{id}-full.jpg`).

## How to collect more examples (manual, repeatable)

1. Open a StreetEasy listing in your browser.
2. Open devtools console.
3. Paste and run `sqft-from-photos/scripts/extract_streeteasy_listing.js`.
4. Copy the printed JSON and append it to the dataset.

To append without manually editing JSON:

```bash
python sqft-from-photos/scripts/append_example.py --payload /path/to/payload.json
```

NYC-only search URL that avoids NJ:
`https://streeteasy.com/for-rent/nyc/area:100,200,300,400,500`

## Download photos

Download a small number of photos per listing into `sqft-from-photos/data/downloads/`:

```bash
python sqft-from-photos/scripts/download_photos.py --max-per-listing 3
```

## Check EXIF frequency

Checks the first N photos per listing (downloads in-memory) and prints a JSON summary:

```bash
python sqft-from-photos/scripts/check_exif.py --max-per-listing 2
```

Finding (as of `2026-01-22`): `0 / 100` photos contained EXIF data when checking the first 5 photos for each of the 20 example listings.

Double-check (downloaded JPGs): after downloading 3 photos per listing (`60` files total), `0 / 60` downloaded JPGs contained EXIF (`python sqft-from-photos/scripts/check_exif_downloads.py`).
