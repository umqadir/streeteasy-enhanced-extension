/**
 * Load Population Data for NTAs
 * Uses ACS 5-year estimates from NYC Planning
 * Download from: https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page
 */

const fs = require('fs');
const path = require('path');
const db = require('../db/connection');

async function loadPopulationData(csvPath) {
  console.log('[Load Population] Loading population data from CSV...');

  try {
    // Read CSV file
    const csvData = fs.readFileSync(csvPath, 'utf8');
    const lines = csvData.split('\n');
    const headers = lines[0].split(',').map(h => h.trim().replace(/"/g, ''));

    console.log(`[Load Population] Found ${lines.length - 1} rows`);

    let inserted = 0;

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;

      const values = line.split(',').map(v => v.trim().replace(/"/g, ''));
      const row = {};

      headers.forEach((header, index) => {
        row[header] = values[index];
      });

      // Extract NTA ID and population
      // Field names may vary - adjust based on your CSV
      const ntaId = row.GeoID || row.NTACode || row.nta_id;
      const population = parseInt(row.Pop_1 || row.Population || row.population || 0);

      if (!ntaId || isNaN(population) || population === 0) {
        continue;
      }

      try {
        await db.query(`
          INSERT INTO nta_population (nta_id, population, acs_year, source)
          VALUES ($1, $2, $3, $4)
          ON CONFLICT (nta_id) DO UPDATE
          SET population = EXCLUDED.population,
              acs_year = EXCLUDED.acs_year,
              source = EXCLUDED.source,
              updated_at = CURRENT_TIMESTAMP
        `, [ntaId, population, '2019-2023', 'NYC Planning ACS 5-year']);

        inserted++;
      } catch (error) {
        console.error(`[Load Population] Error inserting NTA ${ntaId}:`, error.message);
      }
    }

    console.log(`[Load Population] Loaded ${inserted} population records`);

    // Update nta_boundaries table with population
    console.log('[Load Population] Updating nta_boundaries with population...');
    await db.query(`
      UPDATE nta_boundaries n
      SET population = p.population
      FROM nta_population p
      WHERE n.nta_id = p.nta_id
    `);

    console.log('[Load Population] Done!');
    process.exit(0);

  } catch (error) {
    console.error('[Load Population] Error:', error);
    process.exit(1);
  }
}

// Usage: node load-population-data.js <path-to-csv>
const csvPath = process.argv[2];

if (!csvPath) {
  console.error('Usage: node load-population-data.js <path-to-csv>');
  console.error('');
  console.error('Download ACS 5-year population data from:');
  console.error('https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page');
  process.exit(1);
}

loadPopulationData(csvPath);
