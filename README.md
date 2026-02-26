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

## Self-Host NC Bundle (Sqft + Extension)

For a clean non-commercial self-hosted setup (extension + local DUSt3R backend), use:

- `/Users/uzairqadir/Projects/data-projects/national/crimerisk-clone/streeteasy-enhanced-extension/selfhost-nc/README.md`

## Update data

Rebuild the bundled NYC dataset (optional; the extension works with whatever is already in `extension/data/`):

```bash
node scripts/compile-data.js
```

Then reload the extension.

## Data sources

- NYPD Complaint Data (merged): `qgea-i56i` (historic) + `5uac-w243` (current)
- NTA boundaries: `https://data.cityofnewyork.us/resource/9nt8-h7nd.json`

## Disclaimer

Independent project; not affiliated with StreetEasy/Zillow/NYPD/NYC. Complaint counts are not a guarantee of future safety.

---

## CV Pipeline (sqft-from-photos)

Computer vision pipeline for estimating apartment square footage from photos.

### RunPod Setup

**First time setup:**
```bash
bash sqft-from-photos/cv-pipeline/scripts/runpod_bootstrap.sh
source /workspace/cv_pipeline_env.sh
```

This installs:
- System dependencies (COLMAP)
- Python environment (via uv)
- Node.js (via NVM) + Codex + Claude Code CLIs

**After pod restart:**
```bash
source /workspace/cv_pipeline_env.sh
```

The env file persists in `/workspace` and loads NVM automatically, making `node`, `npm`, `codex`, and `claude` available.

### Using Codex

After sourcing the env file:
```bash
codex          # Start Codex CLI
claude         # Start Claude Code CLI
```

### Project Structure

- `sqft-from-photos/cv-pipeline/` - Main pipeline code
- `sqft-from-photos/sample-collection/` - Data collection scripts
- See `sqft-from-photos/cv-pipeline/docs/PROJECT-PLAN.md` for roadmap
