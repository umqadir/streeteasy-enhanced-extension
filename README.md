# SleepEasy

Chrome extension (Manifest V3) for StreetEasy NYC listings. Adds two things to the listing page:

- **Neighborhood crime statistics.** Coordinates from the page are mapped to the NYC Neighborhood Tabulation Area by client-side point-in-polygon, then looked up against precompiled NYPD complaint statistics. Data ships with the extension; no network calls.
- **Room square footage** estimated from listing photos by a local computer-vision backend you run yourself. Photos are fetched and analyzed on your machine.

NYC only. No accounts, no analytics, no tracking.

## Install

### Crime statistics

1. Download `sleepeasy-extension.zip` from the [latest release](https://github.com/umqadir/streeteasy-enhanced-extension/releases/latest) and unzip it.
2. Open `chrome://extensions`, enable Developer mode.
3. Load unpacked → select the `extension` folder.

### Room square footage

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and ~4 GB for model weights.

```bash
cd selfhost
bash scripts/install.sh
bash scripts/start_backend.sh   # http://127.0.0.1:8787
```

Load the extension from `selfhost/extension` and set the backend URL in the side panel. Multi-photo mode needs an NVIDIA GPU; single-image mode runs anywhere. See [selfhost/README.md](selfhost/README.md).

## How it works

Crime: listing coordinates → NYC NTA via point-in-polygon → precompiled per-NTA statistics (NYPD complaints, 2020 Census population, LODES workplace counts), computed client-side. Compiled by `scripts/compile-data.js` and `scripts/compile-nta-exposure.py`. Current data through 2026-03-31.

Square footage: floor segmentation (SegFormer) → metric depth (MoGe-2) → floor-plane fit → area. Multi-photo mode adds DUSt3R multi-view camera fusion.

## Accuracy

Estimates are approximate. Research project; accuracy is not guaranteed.

## Privacy

Crime statistics are fully client-side. Square-footage analysis contacts only `127.0.0.1`. Nothing is collected or transmitted.

## License

MIT. Bundled models retain their own licenses (DUSt3R is non-commercial). Not affiliated with StreetEasy, Zillow Group, the NYPD, or the City of New York.
