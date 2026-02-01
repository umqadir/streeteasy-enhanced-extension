# sample-collection

Quick curation UI (local):

```bash
python sample-collection/scripts/curate_web.py --dataset sample-collection/streeteasy_eval_dataset/listings.json
```

Exports to `sample-collection/clean_set_export/` by default.

If your `listings.json` contains a mix of labeled and unlabeled listings, create a labeled-only subset first:

```bash
python sample-collection/scripts/export_labeled_subset.py \
  --dataset sample-collection/streeteasy_eval_dataset/listings.json
```

Then curate against `sample-collection/streeteasy_eval_dataset/listings_labeled_only.json`.
