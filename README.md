# SleepEasy

Chrome extension (Manifest V3) for StreetEasy NYC listings. Adds two things to the listing page:

- **Neighborhood crime statistics.** Coordinates from the page are mapped to the NYC Neighborhood Tabulation Area by client-side point-in-polygon, then looked up against precompiled NYPD complaint statistics. Data ships with the extension; no network calls.
- **Photo area estimates** from listing photos. Analyze a photo, optionally label it with a room name, and view that photo's floor-area estimate. The model runs **in your browser** with WebGPU and CPU fallback. Photos are analyzed on your device.

NYC only. No accounts, no analytics, no tracking.

## Install

Download `sleepeasy-extension.zip` from the [latest release](https://github.com/umqadir/streeteasy-enhanced-extension/releases/latest), unzip it, open `chrome://extensions`, enable Developer mode, and Load unpacked → select the `extension` folder.

Both features work as-is. Photo area estimates run on-device; the model files (~150 MB) download once from Hugging Face and are cached.

### Optional local backend

The optional local Python backend can run single-photo estimates outside Chrome. Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and local model weights.

```bash
cd selfhost
bash scripts/install.sh
bash scripts/start_backend.sh   # http://127.0.0.1:8787
```

In the side panel settings, set Analysis to use the local backend. See [selfhost/README.md](selfhost/README.md).

## How it works

Crime: listing coordinates → NYC NTA via point-in-polygon → precompiled per-NTA statistics (NYPD complaints, 2020 Census population, LODES workplace counts), computed client-side. Compiled by `scripts/compile-data.js` and `scripts/compile-nta-exposure.py`. Current data through 2026-03-31.

Photo area: floor segmentation (SegFormer) → metric depth (MoGe-2) → floor-plane fit → wall-to-wall layout completion (Manhattan wall-plane fit, with visible-floor fallback). The in-browser path runs both models as ONNX via onnxruntime-web (WebGPU, WASM fallback).

## Accuracy

Estimates are approximate. The model completes the visible photo to its walls when the layout is confident and otherwise falls back to visible floor; error varies with viewing angle and room. Research project; accuracy is not guaranteed.

## Privacy

Crime statistics are fully client-side. Listing photos are fetched and analyzed on-device; the CV model weights download once from Hugging Face. The optional local backend contacts only `127.0.0.1`. No listing or personal data is collected or transmitted.

## License

MIT. Bundled models retain their own licenses. Not affiliated with StreetEasy, Zillow Group, the NYPD, or the City of New York.
