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

## How It Works

1. **Location Detection**: Extracts coordinates from listing maps or geocodes the address using NYC's free GeoSearch API
2. **Neighborhood Mapping**: Maps coordinates to official NYC Neighborhood Tabulation Areas (NTAs)
3. **Crime Statistics**: Shows recent NYPD complaint data with:
   - **Count**: Total incidents in the time period
   - **Rate**: Incidents per 100,000 residents (for fair comparison)
   - **Percentile**: What % of NYC neighborhoods are less safe
   - **Rank**: Position among all NYC neighborhoods
4. **Context**: Provides NYC and borough averages for comparison

## Architecture

### Extension (Frontend)
- **Manifest V3** Chrome extension
- **Content Scripts**: Detect listings, extract coordinates, inject UI
- **Service Worker**: Handle API calls, caching, and side panel
- **Shadow DOM**: Isolated styling to avoid conflicts with StreetEasy

### Backend API
- **Node.js + Express**: RESTful API serving crime statistics
- **PostgreSQL + PostGIS**: Spatial database for NTA boundaries and crime data
- **Data Pipeline**: Daily updates from NYC Open Data (NYPD complaints)

## Installation

### Extension (User Installation)

1. Download or clone this repository
2. Open Chrome and go to `chrome://extensions/`
3. Enable "Developer mode" (top right)
4. Click "Load unpacked" and select the `extension` directory
5. Visit any StreetEasy listing to see crime statistics

### Backend Setup (For Developers)

#### Prerequisites
- Node.js 18+
- PostgreSQL 14+ with PostGIS extension
- NYC Open Data app token (optional but recommended)

#### Database Setup

```bash
# Install PostgreSQL and PostGIS
brew install postgresql postgis  # macOS
# or
sudo apt-get install postgresql postgis  # Ubuntu

# Start PostgreSQL
brew services start postgresql  # macOS
# or
sudo systemctl start postgresql  # Ubuntu

# Create database
createdb streetsafe

# Enable PostGIS
psql streetsafe -c "CREATE EXTENSION postgis;"
```

#### Backend Installation

```bash
cd backend

# Install dependencies
npm install

# Copy environment file
cp .env.example .env

# Edit .env with your database credentials
nano .env

# Initialize database
npm run db:init
```

#### Load Geographic Data

1. **Download NTA Boundaries**
   - Visit: https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq
   - Export as GeoJSON
   - Save as `data/nta-boundaries.geojson`

2. **Download Population Data**
   - Visit: https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page
   - Download ACS 5-year population data for NTAs
   - Save as `data/nta-population.csv`

3. **Load Data**
   ```bash
   node pipeline/load-nta-boundaries.js data/nta-boundaries.geojson
   node pipeline/load-population-data.js data/nta-population.csv
   ```

#### Run Initial Pipeline

```bash
# Fetch and process crime data
npm run pipeline
```

This will:
- Fetch recent NYPD complaint data from NYC Open Data
- Geocode complaints to NTAs
- Compute metrics (counts, rates, percentiles, ranks)
- Calculate NYC and borough averages

#### Start API Server

```bash
npm start
# or for development with auto-reload
npm run dev
```

The API will be available at `http://localhost:3000`

#### Automated Updates

Set up a cron job to run the pipeline daily:

```bash
# Edit crontab
crontab -e

# Add line to run daily at 2 AM
0 2 * * * cd /path/to/streeteasy-enhanced-extension/backend && npm run pipeline
```

## API Documentation

### GET /v1/safety

Get crime statistics for a location.

**Parameters:**
- `lat` (required): Latitude
- `lon` (required): Longitude
- `window` (optional): Time window - `12m` (default), `24m`, or `ytd`

**Example:**
```bash
curl "http://localhost:3000/v1/safety?lat=40.7589&lon=-73.9851&window=12m"
```

**Response:**
```json
{
  "geography": {
    "ntaId": "MN17",
    "ntaName": "Midtown-Midtown South",
    "borough": "Manhattan"
  },
  "metrics": {
    "murder": {
      "count": 2,
      "rate": 5.4,
      "percentile": 45.2,
      "rank": 88,
      "total": 195
    },
    "felonyAssault": {
      "count": 156,
      "rate": 420.3,
      "percentile": 32.1,
      "rank": 133,
      "total": 195
    }
  },
  "timeWindow": "12m",
  "dataThrough": "2026-01-06",
  "computedAt": "2026-01-07T12:00:00.000Z",
  "comparisons": {
    "nycAverage": {
      "murder": 4.2,
      "felonyAssault": 245.6
    }
  },
  "methodologyVersion": "1.0.0"
}
```

## Data Sources

- **Crime Data**: [NYPD Complaint Data](https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Current-Year-To-Date-/5uac-w243) via NYC Open Data
- **Geography**: [NYC Neighborhood Tabulation Areas](https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq) (NTAs)
- **Population**: [ACS 5-year estimates](https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page) from NYC Planning
- **Geocoding**: [NYC GeoSearch API](https://geosearch.planninglabs.nyc/) (Pelias-based, free, no API key required)

## Methodology

### Crime Categories

- **Murder**: NYPD offense description "MURDER & NON-NEGL. MANSLAUGHTER"
- **Felony Assault**: NYPD offense description "FELONY ASSAULT"

### Metrics Explained

1. **Count**: Total number of reported complaints in the time period for that NTA
   - Shows absolute incident volume
   - Important for understanding actual risk in low-population areas

2. **Rate**: (Count / Population) × 100,000
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

⚠️ **Complaint Data ≠ Victimization Risk**

- Reflects *reported* complaints, not all incidents
- Some sensitive crimes (e.g., sexual assaults) may not be geocoded in public data
- Reporting rates vary by neighborhood and demographic factors
- Business districts may show high rates due to daytime population not reflected in residential population estimates
- NTA boundaries are statistical constructs, not "real" neighborhood boundaries
- Population estimates may be outdated for rapidly changing areas
- Past crime data is not a guarantee of future safety conditions

**Use this as ONE factor among many when evaluating a location.**

## Privacy & Data Collection

- **No Browsing History**: We only process the current listing page
- **Minimal Retention**: Backend does not log specific coordinates by default
- **Local Caching**: Statistics are cached in browser storage for 24 hours
- **No User Accounts**: No registration or personal information required
- **Open Source**: All code is auditable

## Development

### Project Structure

```
streeteasy-enhanced-extension/
├── extension/              # Chrome extension
│   ├── manifest.json       # Extension manifest (V3)
│   ├── content/            # Content scripts
│   ├── background/         # Service worker
│   ├── ui/                 # UI components
│   └── lib/                # Shared utilities
├── backend/                # Backend API
│   ├── api/                # Express API server
│   ├── db/                 # Database connection and schema
│   └── pipeline/           # Data pipeline scripts
└── docs/                   # Documentation
```

### Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

### Testing Locally

1. **Test Extension**:
   - Load unpacked extension in Chrome
   - Visit a StreetEasy listing
   - Check browser console for logs

2. **Test Backend**:
   ```bash
   # Test API endpoint
   curl "http://localhost:3000/v1/safety?lat=40.7589&lon=-73.9851"

   # Check database
   psql streetsafe -c "SELECT COUNT(*) FROM crime_complaints;"
   ```

## Roadmap

### MVP (Current)
- [x] Inline crime module on listing pages
- [x] Side panel with detailed statistics
- [x] Murder and felony assault metrics
- [x] NTA-based geography
- [x] Backend API with caching
- [x] Data pipeline for NYPD complaints
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
- [ ] Mobile app version

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

**Made with ❤️ for NYC apartment hunters**
