# sample-collection

## What stays here

- Full source dataset: `sample-collection/streeteasy_eval_dataset/listings.json`
- Source photos: `sample-collection/streeteasy_eval_dataset/photos/...`
- Current export (always same path): `sample-collection/clean_set_export/listings.json`
- Export photos: `sample-collection/clean_set_export/photos/...`
- Previous exports (auto-archived): `sample-collection/clean_set_archive/export_<timestamp>/...`
- Collection/scraping scripts: `sample-collection/scripts/legacy/`

## Export workflow

Run the curation UI against the full source dataset:

```bash
python sample-collection/scripts/curate_web.py
```

On the first successful export in that run, previous `clean_set_export` contents are moved to `clean_set_archive`.
New export output is then written back to `clean_set_export`, so downstream paths stay stable.
