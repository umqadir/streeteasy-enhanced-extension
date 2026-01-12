# SleepEasy

Adds a small, inline “Crime” module to StreetEasy NYC listings.

- Metrics: **Felony Assault**, **Property Crime**, **Murder** (last **24 months**)
- Measures: **Ambient risk index** (default), **Per 100k residents**, **Per sq mi**, **Raw incidents**
- Location: uses the listing’s own Google Maps coordinate link (no geocoding)
- Geography: NYC **NTA** (Neighborhood Tabulation Area) containing the listing

## Install

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select the `extension/` folder
4. Visit a StreetEasy listing page (e.g. `/building/...`, `/rental/...`, `/sale/...`)

After any code/data changes: `chrome://extensions` → **Reload** SleepEasy → refresh the listing tab.

## Update data

Rebuild the bundled NYC dataset (optional; the extension works with whatever is already in `extension/data/`):

```bash
node scripts/compile-data.js
```

Then reload the extension.

## Data sources

- NYPD Complaint Data: `https://data.cityofnewyork.us/resource/5uac-w243.json`
- NTA boundaries: `https://data.cityofnewyork.us/resource/9nt8-h7nd.json`

## Disclaimer

Independent project; not affiliated with StreetEasy/Zillow/NYPD/NYC. Complaint counts are not a guarantee of future safety.
