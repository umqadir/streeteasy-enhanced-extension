# StreetSafe - Crime Context for StreetEasy

A Chrome extension that adds neighborhood crime statistics and safety context to StreetEasy apartment listings, helping you make more informed decisions about where to live.

![StreetSafe Demo](docs/screenshot.png)

## Features

- **Native Integration**: Crime statistics appear directly on StreetEasy listing pages
- **Comprehensive Data**: Shows murder and felony assault rates with both absolute counts and per-capita rates
- **Smart Rankings**: Compare neighborhoods using percentiles and ranks relative to all NYC areas
- **Multiple Views**: Inline module on listing pages + detailed side panel for methodology
- **Geocoding Fallback**: Uses NYC GeoSearch API when coordinates aren't directly available
- **Rate + Count Metrics**: Shows both population-adjusted rates AND absolute figures to account for low-population/high-crime business districts
- **Time Windows**: View data for last 12 months, 24 months, or calendar year
- **Privacy First**: No browsing history collection, minimal data retention
- **No Backend Required**: All data is precompiled and bundled with the extension

## How It Works

1. **Location Detection**: Extracts coordinates from listing maps or geocodes the address using NYC's free GeoSearch API
2. **Neighborhood Mapping**: Maps coordinates to official NYC Neighborhood Tabulation Areas (NTAs) using client-side point-in-polygon lookup
3. **Crime Statistics**: Shows precompiled NYPD complaint data with:
   - **Count**: Total incidents in the time period
   - **Rate**: Incidents per 100,000 residents (for fair comparison)
   - **Percentile**: What % of NYC neighborhoods are less safe
   - **Rank**: Position among all NYC neighborhoods
4. **Context**: Provides NYC and borough averages for comparison

## Architecture

### Simplified Client-Side Design

This extension uses a **fully client-side architecture** with precompiled static data. No backend server is required for end users.

```
Extension (Chrome MV3)
├── Content Scripts     → Detect listings, extract coordinates
├── Geo Utilities       → Point-in-polygon NTA lookup
├── Static Data         → Precompiled NTA boundaries + crime stats
├── Service Worker      → Handle caching and side panel
└── Shadow DOM UI       → Isolated styling for injected components

Data Compilation (Offline)
└── scripts/compile-data.js → Fetches from NYC Open Data, computes metrics
```

### Why No Runtime Backend?

- **Cost Effective**: No server hosting costs for maintainers or users
- **Fast**: All lookups happen locally, no network latency
- **Private**: User locations never leave their browser
- **Offline Capable**: Works without an internet connection (after initial page load)
- **Scalable**: Works for any number of users without infrastructure scaling

### Data Freshness

Crime statistics are precomputed and bundled with the extension. To update the data:
1. Run `node scripts/compile-data.js` to fetch fresh data from NYC Open Data
2. The script generates updated `extension/data/*.json` files
3. Reload the extension to use the new data

Data is typically updated quarterly (matching NYPD's release cycle).

## Installation

### User Installation

1. Download or clone this repository
2. Open Chrome and go to `chrome://extensions/`
3. Enable "Developer mode" (top right)
4. Click "Load unpacked" and select the `extension` directory
5. Visit any StreetEasy listing to see crime statistics

### Data Update (For Maintainers)

To refresh the precompiled crime statistics:

```bash
# Run the data compiler
node scripts/compile-data.js
```

This will:
- Fetch NTA boundaries from NYC Open Data
- Fetch recent NYPD complaint data
- Compute metrics (counts, rates, percentiles, ranks) for all NTAs
- Generate `extension/data/nta-boundaries.json` and `extension/data/crime-stats.json`

**Prerequisites for data compilation:**
- Node.js 18+
- Internet connection to access NYC Open Data APIs

The extension will work immediately after installation using the bundled data files. Data compilation is only needed when you want to refresh the statistics.

## Data Sources

- **Crime Data**: [NYPD Complaint Data](https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Current-Year-To-Date-/5uac-w243) via NYC Open Data
- **Geography**: [NYC Neighborhood Tabulation Areas](https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq) (NTAs)
- **Population**: ACS 5-year estimates from NYC Planning
- **Geocoding**: [NYC GeoSearch API](https://geosearch.planninglabs.nyc/) (Pelias-based, free, no API key required)

## Methodology

### Crime Categories

- **Murder**: NYPD offense description "MURDER & NON-NEGL. MANSLAUGHTER"
- **Felony Assault**: NYPD offense description "FELONY ASSAULT"

### Metrics Explained

1. **Count**: Total number of reported complaints in the time period for that NTA
   - Shows absolute incident volume
   - Important for understanding actual risk in low-population areas

2. **Rate**: (Count / Population) x 100,000
   - Normalizes for population size
   - Enables fair comparison between large and small neighborhoods

3. **Percentile**: Percentage of NYC neighborhoods with a *lower* rate
   - Higher percentile = safer relative to other areas
   - Example: 75% means safer than 75% of NYC neighborhoods

4. **Rank**: Position when all NTAs are sorted by rate (ascending)
   - Rank 1 = lowest crime rate in NYC
   - Rank 195 = highest crime rate in NYC

### Why Both Rates and Counts?

We show both metrics because:
- **Rates** adjust for population, enabling fair comparison
- **Counts** reveal actual incident volume, which matters for:
  - Low-population business districts (may have high rates but moderate absolute crime)
  - Understanding your actual risk exposure
  - Identifying areas where a single incident skews the rate dramatically

### Geography: Neighborhood Tabulation Areas (NTAs)

NTAs are statistical geographies created by NYC Planning for Census data reporting. They:
- Approximate neighborhoods but prioritize statistical consistency
- Don't always match subjective "neighborhood" boundaries
- Provide stable geographies for year-over-year comparison
- Are the standard unit for NYC demographic and statistical analysis

### Important Limitations

**Complaint Data != Victimization Risk**

- Reflects *reported* complaints, not all incidents
- Some sensitive crimes (e.g., sexual assaults) may not be geocoded in public data
- Reporting rates vary by neighborhood and demographic factors
- Business districts may show high rates due to daytime population not reflected in residential population estimates
- NTA boundaries are statistical constructs, not "real" neighborhood boundaries
- Population estimates may be outdated for rapidly changing areas
- Past crime data is not a guarantee of future safety conditions

**Use this as ONE factor among many when evaluating a location.**

## Privacy & Data Collection

- **No Server**: All data lookups happen locally in your browser
- **No Browsing History**: We only process the current listing page
- **No Location Tracking**: Your coordinates are never sent to any server
- **Local Caching**: Statistics are cached in browser storage for 24 hours
- **No User Accounts**: No registration or personal information required
- **Open Source**: All code is auditable

## Development

### Project Structure

```
streeteasy-enhanced-extension/
├── extension/              # Chrome extension (user-facing)
│   ├── manifest.json       # Extension manifest (V3)
│   ├── background/         # Service worker
│   ├── content/            # Content scripts
│   ├── lib/                # Shared utilities (geo-utils, utils)
│   ├── ui/                 # UI components (side panel, styles)
│   ├── data/               # Precompiled static data
│   │   ├── nta-boundaries.json
│   │   └── crime-stats.json
│   └── icons/              # Extension icons
├── scripts/                # Data compilation tools
│   └── compile-data.js     # Fetches and compiles NYC data
├── backend/                # [ARCHIVED] Legacy backend (not required)
└── docs/                   # Documentation
```

### Key Files

| File | Purpose |
|------|---------|
| `extension/lib/geo-utils.js` | Point-in-polygon NTA lookup, crime stats manager |
| `extension/lib/utils.js` | Geocoding, formatting, caching utilities |
| `extension/content/main.js` | Main orchestrator for content scripts |
| `extension/content/coordinates-extractor.js` | Extracts lat/lon from StreetEasy pages |
| `extension/content/ui-injector.js` | Injects crime stats UI into pages |
| `extension/data/*.json` | Precompiled NTA boundaries and crime statistics |
| `scripts/compile-data.js` | Data compilation script for maintainers |

### Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

### Testing Locally

1. **Test Extension**:
   - Load unpacked extension in Chrome (`chrome://extensions/`)
   - Visit a StreetEasy listing
   - Check browser console for logs (filter by "StreetSafe")

2. **Update Data**:
   ```bash
   # Compile fresh data from NYC Open Data
   node scripts/compile-data.js

   # Reload the extension in Chrome
   ```

## Roadmap

### MVP (Current)
- [x] Inline crime module on listing pages
- [x] Side panel with detailed statistics
- [x] Murder and felony assault metrics
- [x] NTA-based geography
- [x] Client-side point-in-polygon NTA lookup
- [x] Precompiled static data (no backend required)
- [x] Geocoding fallback with NYC API
- [x] Both rate and count metrics

### V1.1 (Planned)
- [ ] Additional crime categories (robbery, burglary)
- [ ] Trend visualization (charts over time)
- [ ] Neighborhood comparison tool
- [ ] Export statistics to PDF
- [ ] Chrome Web Store listing

### V2.0 (Future)
- [ ] Support for other listing sites (Zillow, Trulia, etc.)
- [ ] Historical trend analysis
- [ ] Customizable time windows
- [ ] User preferences and settings

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Disclaimer

This extension is an independent project and is not affiliated with, endorsed by, or sponsored by StreetEasy, Zillow Group, the NYPD, or NYC government.

Crime statistics are based on publicly available NYPD complaint data and should be used as ONE factor among many when evaluating a neighborhood. Past crime data does not guarantee future safety conditions.

## Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/streeteasy-enhanced-extension/issues)
- **Questions**: Open a discussion or contact via GitHub

## Acknowledgments

- NYC Open Data for making NYPD complaint data publicly available
- NYC Planning for NTA boundaries and population data
- NYC GeoSearch (Pelias) for free geocoding
- Chrome Extension community for documentation and examples

---

**Made with care for NYC apartment hunters**
