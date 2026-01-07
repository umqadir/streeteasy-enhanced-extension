/**
 * Load NTA Boundaries from NYC Planning GeoJSON
 * Download from: https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq
 */

const fs = require('fs');
const path = require('path');
const db = require('../db/connection');

async function loadNTABoundaries(geojsonPath) {
  console.log('[Load NTA] Loading NTA boundaries from GeoJSON...');

  try {
    // Read GeoJSON file
    const geojsonData = JSON.parse(fs.readFileSync(geojsonPath, 'utf8'));
    console.log(`[Load NTA] Found ${geojsonData.features.length} NTAs`);

    let inserted = 0;

    for (const feature of geojsonData.features) {
      const { properties, geometry } = feature;

      // Extract properties (field names may vary by dataset version)
      const ntaId = properties.ntacode || properties.NTACode || properties.nta_id;
      const ntaName = properties.ntaname || properties.NTAName || properties.nta_name;
      const borough = properties.boro_name || properties.BoroName || properties.borough;

      if (!ntaId || !geometry) {
        console.warn('[Load NTA] Skipping feature with missing data');
        continue;
      }

      // Convert geometry to WKT for PostGIS
      const geomJSON = JSON.stringify(geometry);

      try {
        await db.query(`
          INSERT INTO nta_boundaries (nta_id, nta_name, borough, geom)
          VALUES ($1, $2, $3, ST_SetSRID(ST_GeomFromGeoJSON($4), 4326))
          ON CONFLICT (nta_id) DO UPDATE
          SET nta_name = EXCLUDED.nta_name,
              borough = EXCLUDED.borough,
              geom = EXCLUDED.geom
        `, [ntaId, ntaName, borough, geomJSON]);

        inserted++;
      } catch (error) {
        console.error(`[Load NTA] Error inserting NTA ${ntaId}:`, error.message);
      }
    }

    console.log(`[Load NTA] Loaded ${inserted} NTA boundaries`);

    // Create spatial index
    console.log('[Load NTA] Creating spatial index...');
    await db.query('CREATE INDEX IF NOT EXISTS idx_nta_geom ON nta_boundaries USING GIST(geom)');

    console.log('[Load NTA] Done!');
    process.exit(0);

  } catch (error) {
    console.error('[Load NTA] Error:', error);
    process.exit(1);
  }
}

// Usage: node load-nta-boundaries.js <path-to-geojson>
const geojsonPath = process.argv[2];

if (!geojsonPath) {
  console.error('Usage: node load-nta-boundaries.js <path-to-geojson>');
  console.error('');
  console.error('Download NTA boundaries from:');
  console.error('https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq');
  process.exit(1);
}

loadNTABoundaries(geojsonPath);
