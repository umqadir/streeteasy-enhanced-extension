# StreetSafe Setup Guide

Complete step-by-step guide to set up StreetSafe from scratch.

## Prerequisites

Before starting, ensure you have:

- [ ] Chrome browser (or Chromium-based browser)
- [ ] Node.js 18+ (`node --version`)
- [ ] PostgreSQL 14+ (`psql --version`)
- [ ] PostGIS extension
- [ ] Basic command line knowledge
- [ ] 2-3 GB free disk space for database

## Part 1: Database Setup

### 1.1 Install PostgreSQL and PostGIS

**macOS (Homebrew):**
```bash
brew install postgresql@14 postgis
brew services start postgresql@14
```

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install postgresql-14 postgresql-14-postgis-3
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**Windows:**
- Download PostgreSQL from https://www.postgresql.org/download/windows/
- During installation, select PostGIS from the Stack Builder

### 1.2 Create Database

```bash
# Create database
createdb streetsafe

# Connect and enable PostGIS
psql streetsafe
```

In psql:
```sql
CREATE EXTENSION postgis;
\q
```

Verify PostGIS:
```bash
psql streetsafe -c "SELECT PostGIS_Version();"
```

You should see version information (e.g., "3.3 USE_GEOS=1...").

## Part 2: Backend Setup

### 2.1 Install Dependencies

```bash
cd backend
npm install
```

### 2.2 Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=streetsafe
DB_USER=postgres
DB_PASSWORD=your_password_here

PORT=3000
NODE_ENV=development

# Optional: Get app token from https://data.cityofnewyork.us/
NYC_OPEN_DATA_APP_TOKEN=
```

### 2.3 Initialize Database

```bash
npm run db:init
```

You should see:
```
[Database Init] Starting initialization...
[Database Init] Creating tables...
[Database Init] Tables created successfully
[Database Init] PostGIS version: 3.3...
[Database Init] Initialization complete!
```

### 2.4 Verify Database Schema

```bash
psql streetsafe -c "\dt"
```

You should see these tables:
- `crime_complaints`
- `crime_comparisons`
- `crime_metrics`
- `nta_boundaries`
- `nta_population`
- `pipeline_metadata`

## Part 3: Load Geographic Data

### 3.1 Download NTA Boundaries

1. Visit: https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq
2. Click "Export" → "GeoJSON"
3. Save to `backend/data/nta-boundaries.geojson`

Or use curl:
```bash
mkdir -p backend/data
curl -o backend/data/nta-boundaries.geojson \
  "https://data.cityofnewyork.us/resource/cpf4-rkhq.geojson?\$limit=300"
```

### 3.2 Load NTA Boundaries

```bash
cd backend
node pipeline/load-nta-boundaries.js data/nta-boundaries.geojson
```

Expected output:
```
[Load NTA] Loading NTA boundaries from GeoJSON...
[Load NTA] Found 195 NTAs
[Load NTA] Loaded 195 NTA boundaries
[Load NTA] Creating spatial index...
[Load NTA] Done!
```

### 3.3 Download Population Data

1. Visit: https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page
2. Download "NTA-level ACS 5-year" population data
3. Save to `backend/data/nta-population.csv`

Or use pre-processed data:
```bash
# This is a simplified version - you may need to adjust field names
cat > backend/data/nta-population.csv << 'EOF'
GeoID,Population
MN01,63000
MN02,82000
...
EOF
```

### 3.4 Load Population Data

```bash
node pipeline/load-population-data.js data/nta-population.csv
```

Expected output:
```
[Load Population] Loading population data from CSV...
[Load Population] Found 195 rows
[Load Population] Loaded 195 population records
[Load Population] Updating nta_boundaries with population...
[Load Population] Done!
```

### 3.5 Verify Geographic Data

```bash
psql streetsafe -c "SELECT COUNT(*) FROM nta_boundaries;"
psql streetsafe -c "SELECT COUNT(*) FROM nta_population;"
```

Both should return 195 (or similar, depending on NTA version).

Test spatial query:
```bash
psql streetsafe -c "
  SELECT nta_id, nta_name, borough
  FROM nta_boundaries
  WHERE ST_Contains(
    geom,
    ST_SetSRID(ST_MakePoint(-73.9851, 40.7589), 4326)
  );"
```

Should return Times Square area NTA.

## Part 4: Load Crime Data

### 4.1 Get NYC Open Data App Token (Optional)

1. Visit: https://data.cityofnewyork.us/
2. Sign up for a free account
3. Go to Developer Settings
4. Create an app token
5. Add to `.env`: `NYC_OPEN_DATA_APP_TOKEN=your_token`

**Note**: App token is optional but increases rate limits from 1,000 to 10,000 requests/day.

### 4.2 Run Initial Pipeline

```bash
npm run pipeline
```

This will:
1. Fetch recent NYPD complaint data (50,000 records max per run)
2. Filter for murder and felony assault
3. Geocode complaints to NTAs
4. Compute metrics for each time window (12m, 24m, ytd)
5. Calculate NYC and borough averages

Expected output:
```
[Pipeline] Starting data update...
[Pipeline] Fetching latest complaints from NYC Open Data...
[Pipeline] Fetched 12543 new complaints
[Pipeline] Inserting complaints...
[Pipeline] Inserted 12543 new complaints
[Pipeline] Associating complaints with NTAs...
[Pipeline] Computing metrics for 12m...
[Pipeline] Computing metrics for 24m...
[Pipeline] Computing metrics for ytd...
[Pipeline] Computing comparisons...
[Pipeline] Pipeline completed successfully!
```

**First run may take 10-30 minutes** depending on data volume and network speed.

### 4.3 Verify Crime Data

```bash
psql streetsafe -c "SELECT COUNT(*) FROM crime_complaints;"
psql streetsafe -c "SELECT COUNT(*) FROM crime_metrics;"
```

Check a specific NTA:
```bash
psql streetsafe -c "
  SELECT nta_id, metric_type, count, rate, percentile, rank
  FROM crime_metrics
  WHERE nta_id = 'MN17' AND time_window = '12m'
  ORDER BY metric_type;"
```

## Part 5: Start Backend API

### 5.1 Start Server

```bash
npm start
# or for development with auto-reload:
npm run dev
```

Expected output:
```
[StreetSafe API] Server running on port 3000
[StreetSafe API] Environment: development
[Database] Connected to PostgreSQL
```

### 5.2 Test API

In another terminal:

```bash
# Health check
curl http://localhost:3000/health

# Get statistics for Times Square
curl "http://localhost:3000/v1/safety?lat=40.7589&lon=-73.9851&window=12m"
```

Should return JSON with crime statistics.

## Part 6: Install Extension

### 6.1 Load Extension in Chrome

1. Open Chrome
2. Go to `chrome://extensions/`
3. Enable "Developer mode" (toggle in top-right)
4. Click "Load unpacked"
5. Select the `extension` directory from this repository
6. Extension should appear in your extensions list

### 6.2 Verify Extension

1. Visit any StreetEasy listing (e.g., https://streeteasy.com/building/...)
2. Look for the "Crime & Safety" module below the map
3. Open browser console (F12) and check for StreetSafe logs
4. Click the extension icon to open the side panel

## Part 7: Schedule Automated Updates

### 7.1 Create Update Script

```bash
cat > backend/update.sh << 'EOF'
#!/bin/bash
cd /path/to/streeteasy-enhanced-extension/backend
npm run pipeline >> logs/pipeline.log 2>&1
EOF

chmod +x backend/update.sh
mkdir -p backend/logs
```

### 7.2 Add to Crontab

```bash
crontab -e
```

Add this line to run daily at 2 AM:
```
0 2 * * * /path/to/streeteasy-enhanced-extension/backend/update.sh
```

Verify:
```bash
crontab -l
```

## Troubleshooting

### Database Connection Error

**Error**: `ECONNREFUSED` or `password authentication failed`

**Solution**:
1. Check PostgreSQL is running: `pg_isready`
2. Verify credentials in `.env`
3. Check pg_hba.conf allows local connections
4. Restart PostgreSQL: `brew services restart postgresql` (macOS)

### PostGIS Not Found

**Error**: `extension "postgis" does not exist`

**Solution**:
```bash
# macOS
brew install postgis

# Ubuntu
sudo apt-get install postgresql-14-postgis-3

# Then reconnect and enable:
psql streetsafe -c "CREATE EXTENSION postgis;"
```

### Pipeline Fails to Fetch Data

**Error**: `429 Too Many Requests` or network errors

**Solution**:
1. Get an NYC Open Data app token
2. Add to `.env`: `NYC_OPEN_DATA_APP_TOKEN=your_token`
3. Wait a few minutes and retry
4. Check internet connection and firewall

### No Data in crime_complaints

**Error**: Pipeline runs but inserts 0 records

**Solution**:
1. Check date range in pipeline (may be fetching old data)
2. Verify offense descriptions match NYPD format
3. Check NYC Open Data API is accessible:
   ```bash
   curl "https://data.cityofnewyork.us/resource/5uac-w243.json?\$limit=1"
   ```

### Extension Not Detecting Listings

**Problem**: Module doesn't appear on StreetEasy pages

**Solution**:
1. Check browser console for errors
2. Verify you're on a listing page (URL contains `/building/`, `/rental/`, or `/sale/`)
3. Check content script is injected: Look for `[StreetSafe]` logs in console
4. Reload extension: `chrome://extensions/` → Reload
5. Verify backend API is running: `curl http://localhost:3000/health`

### Extension Shows "Could Not Load"

**Problem**: Module shows error message

**Solution**:
1. Check backend API is running: `http://localhost:3000/health`
2. Check API URL in `extension/lib/utils.js` matches your backend
3. Look for CORS errors in console - backend should allow extension origin
4. Check database has data: `psql streetsafe -c "SELECT COUNT(*) FROM crime_metrics;"`

## Verification Checklist

After setup, verify everything works:

- [ ] PostgreSQL is running
- [ ] PostGIS extension is enabled
- [ ] Database tables exist (6 tables)
- [ ] NTA boundaries loaded (195 records)
- [ ] Population data loaded (195 records)
- [ ] Crime complaints loaded (thousands of records)
- [ ] Crime metrics computed (195 × 3 × 2 = 1170 records minimum)
- [ ] Backend API responds to health check
- [ ] Backend API returns crime statistics
- [ ] Extension loads in Chrome
- [ ] Extension detects StreetEasy listings
- [ ] Crime module appears on listing pages
- [ ] Side panel opens and shows data

## Next Steps

Once everything is working:

1. **Customize**: Adjust colors, styling, or metrics in extension code
2. **Optimize**: Add database indexes for better performance
3. **Monitor**: Set up logging and error tracking
4. **Deploy**: Consider deploying backend to a cloud service for 24/7 availability
5. **Publish**: Submit extension to Chrome Web Store (requires review)

## Getting Help

- Check the main [README.md](../README.md)
- Review [METHODOLOGY.md](./METHODOLOGY.md) for data details
- Open an issue on GitHub
- Check browser and server logs for error messages

---

Setup complete! 🎉 You should now have a fully functional StreetSafe installation.
