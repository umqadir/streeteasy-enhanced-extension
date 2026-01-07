/**
 * Database Initialization Script
 * Creates tables and loads initial data
 */

const fs = require('fs');
const path = require('path');
const db = require('./connection');

async function initialize() {
  console.log('[Database Init] Starting initialization...');

  try {
    // Read and execute schema
    const schemaSQL = fs.readFileSync(
      path.join(__dirname, 'schema.sql'),
      'utf8'
    );

    console.log('[Database Init] Creating tables...');
    await db.query(schemaSQL);
    console.log('[Database Init] Tables created successfully');

    // Check PostGIS
    const postgisCheck = await db.query(`
      SELECT PostGIS_Version();
    `);
    console.log('[Database Init] PostGIS version:', postgisCheck.rows[0].postgis_version);

    console.log('[Database Init] Initialization complete!');
    console.log('[Database Init] Next steps:');
    console.log('  1. Load NTA boundary data (GeoJSON/Shapefile)');
    console.log('  2. Load population data (ACS 5-year)');
    console.log('  3. Run initial pipeline: npm run pipeline');

    process.exit(0);

  } catch (error) {
    console.error('[Database Init] Error:', error);
    process.exit(1);
  }
}

initialize();
