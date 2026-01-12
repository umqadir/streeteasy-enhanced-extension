# SleepEasy — Maintainers

This is for development/debugging/release notes. End-user usage lives in `README.md`.

## Debugging

- Enable logs (default is silent): in the StreetEasy tab DevTools console run `globalThis.__SLEEPEASY_DEBUG__ = true` and refresh.
- Find the injected module: `#streetsafe-module` (Shadow DOM host).
- Isolate extension issues: filter the console by `chrome-extension://` (StreetEasy itself is noisy).

## Data + metrics

- `scripts/compile-data.js` generates:
  - `extension/data/crime-stats.json` (currently only `24m`)
  - `extension/data/nta-boundaries.json`
- `scripts/compile-data.js` merges NYPD complaint data from NYC Open Data historic + current-year datasets (deduped by `cmplnt_num`) to keep rolling windows accurate.
- Crime categories are defined in `scripts/compile-data.js`:
  - `murder`: `MURDER & NON-NEGL. MANSLAUGHTER`
  - `felonyAssault`: `FELONY ASSAULT`
  - `propertyCrime`: `BURGLARY`, `GRAND LARCENY`, `GRAND LARCENY OF MOTOR VEHICLE`
- Ambient risk index denominator uses resident population + LODES WAC jobs (see `computeAmbientPopulation` in `extension/lib/utils.js`).
- `scripts/compile-nta-exposure.py` generates `extension/data/nta-exposure.json` and expects local inputs under `../data/` (Census + LODES parquet). If Python deps are missing, install via `uv pip install ...` (no bare `pip`).

## Debug map

Interactive murder map (by NTA) for sanity checks:

- Run: `node scripts/serve-debug-map.js`
- Open: `http://localhost:4173/docs/debug-murder-map.html`

## Chrome Web Store sanity

- Keep permissions minimal (no `storage`, no broad host permissions unless needed).
- No remote code in the extension bundle (all extension JS/CSS is local; data is bundled JSON).
- Before packaging, smoke-test a listing page and confirm there are no `chrome-extension://...` console errors.
