/**
 * StreetSafe Data Pipeline
 * Fetches NYPD complaint data and computes crime statistics
 */

const axios = require('axios');
const db = require('../db/connection');
require('dotenv').config();

const SOCRATA_ENDPOINT = 'https://data.cityofnewyork.us/resource/5uac-w243.json';
const APP_TOKEN = process.env.NYC_OPEN_DATA_APP_TOKEN;

// Crime categories to track
const CRIME_CATEGORIES = {
  murder: 'MURDER & NON-NEGL. MANSLAUGHTER',
  felonyAssault: 'FELONY ASSAULT'
};

/**
 * Main pipeline execution
 */
async function runPipeline() {
  console.log('[Pipeline] Starting data update...');

  const runId = await startPipelineRun('incremental');

  try {
    // Step 1: Fetch latest complaints
    console.log('[Pipeline] Fetching latest complaints from NYC Open Data...');
    const newComplaints = await fetchLatestComplaints();
    console.log(`[Pipeline] Fetched ${newComplaints.length} new complaints`);

    // Step 2: Insert complaints into database
    console.log('[Pipeline] Inserting complaints...');
    const inserted = await insertComplaints(newComplaints);
    console.log(`[Pipeline] Inserted ${inserted} new complaints`);

    // Step 3: Geocode complaints to NTAs
    console.log('[Pipeline] Associating complaints with NTAs...');
    await geocodeComplaintsToNTAs();

    // Step 4: Compute metrics for each time window
    for (const window of ['12m', '24m', 'ytd']) {
      console.log(`[Pipeline] Computing metrics for ${window}...`);
      await computeMetrics(window);
    }

    // Step 5: Compute comparisons (NYC and borough averages)
    console.log('[Pipeline] Computing comparisons...');
    await computeComparisons();

    await completePipelineRun(runId, inserted);
    console.log('[Pipeline] Pipeline completed successfully!');

  } catch (error) {
    console.error('[Pipeline] Error:', error);
    await failPipelineRun(runId, error.message);
    process.exit(1);
  }

  process.exit(0);
}

/**
 * Fetch latest complaints from NYC Open Data
 */
async function fetchLatestComplaints() {
  try {
    // Get last complaint date from database
    const lastDateResult = await db.query(`
      SELECT MAX(complaint_date) as last_date
      FROM crime_complaints
    `);

    const lastDate = lastDateResult.rows[0]?.last_date;
    const startDate = lastDate || new Date('2023-01-01');

    // Build Socrata query
    const where = `cmplnt_fr_dt > '${formatDate(startDate)}'`;
    const offenseFilter = Object.values(CRIME_CATEGORIES)
      .map(cat => `ofns_desc = '${cat}'`)
      .join(' OR ');

    const query = `
      SELECT
        cmplnt_num,
        cmplnt_fr_dt,
        ofns_desc,
        law_cat_cd,
        latitude,
        longitude
      WHERE (${offenseFilter})
        AND ${where}
        AND latitude IS NOT NULL
        AND longitude IS NOT NULL
      ORDER BY cmplnt_fr_dt DESC
      LIMIT 50000
    `.trim().replace(/\s+/g, ' ');

    const params = {
      $query: query
    };

    if (APP_TOKEN) {
      params['$$app_token'] = APP_TOKEN;
    }

    const response = await axios.get(SOCRATA_ENDPOINT, { params });

    return response.data;

  } catch (error) {
    console.error('[Pipeline] Error fetching complaints:', error.message);
    throw error;
  }
}

/**
 * Insert complaints into database
 */
async function insertComplaints(complaints) {
  let inserted = 0;

  for (const complaint of complaints) {
    try {
      await db.query(`
        INSERT INTO crime_complaints (
          complaint_num,
          complaint_date,
          offense_desc,
          law_cat_cd,
          latitude,
          longitude,
          geom
        ) VALUES ($1, $2, $3, $4, $5, $6, ST_SetSRID(ST_MakePoint($6, $5), 4326))
        ON CONFLICT (complaint_num) DO NOTHING
      `, [
        complaint.cmplnt_num,
        complaint.cmplnt_fr_dt,
        complaint.ofns_desc,
        complaint.law_cat_cd,
        parseFloat(complaint.latitude),
        parseFloat(complaint.longitude)
      ]);

      inserted++;
    } catch (error) {
      console.error('[Pipeline] Error inserting complaint:', error.message);
    }
  }

  return inserted;
}

/**
 * Geocode complaints to NTAs using spatial join
 */
async function geocodeComplaintsToNTAs() {
  await db.query(`
    UPDATE crime_complaints c
    SET nta_id = n.nta_id
    FROM nta_boundaries n
    WHERE c.nta_id IS NULL
      AND ST_Contains(n.geom, c.geom)
  `);
}

/**
 * Compute metrics for a time window
 */
async function computeMetrics(window) {
  const dateRange = getDateRangeForWindow(window);

  for (const [metricKey, offenseDesc] of Object.entries(CRIME_CATEGORIES)) {
    // Compute counts and rates per NTA
    await db.query(`
      INSERT INTO crime_metrics (
        nta_id,
        time_window,
        metric_type,
        count,
        rate,
        percentile,
        rank,
        total_neighborhoods,
        computed_at
      )
      WITH nta_stats AS (
        SELECT
          n.nta_id,
          COUNT(c.id) as count,
          COALESCE(p.population, 1) as population,
          (COUNT(c.id)::NUMERIC / NULLIF(p.population, 0) * 100000) as rate
        FROM nta_boundaries n
        LEFT JOIN crime_complaints c ON c.nta_id = n.nta_id
          AND c.offense_desc = $1
          AND c.complaint_date >= $2
          AND c.complaint_date <= $3
        LEFT JOIN nta_population p ON p.nta_id = n.nta_id
        GROUP BY n.nta_id, p.population
      ),
      ranked AS (
        SELECT
          nta_id,
          count,
          rate,
          PERCENT_RANK() OVER (ORDER BY rate DESC) * 100 as percentile,
          ROW_NUMBER() OVER (ORDER BY rate ASC) as rank,
          COUNT(*) OVER () as total
        FROM nta_stats
        WHERE population > 0
      )
      SELECT
        nta_id,
        $4 as time_window,
        $5 as metric_type,
        count,
        rate,
        (100 - percentile) as percentile,
        rank,
        total,
        CURRENT_TIMESTAMP
      FROM ranked
      ON CONFLICT (nta_id, time_window, metric_type, computed_at) DO NOTHING
    `, [offenseDesc, dateRange.start, dateRange.end, window, metricKey]);
  }
}

/**
 * Compute NYC-wide and borough averages
 */
async function computeComparisons() {
  for (const window of ['12m', '24m', 'ytd']) {
    const dateRange = getDateRangeForWindow(window);

    for (const [metricKey, offenseDesc] of Object.entries(CRIME_CATEGORIES)) {
      // NYC average
      await db.query(`
        INSERT INTO crime_comparisons (
          time_window,
          metric_type,
          nyc_average,
          computed_at
        )
        SELECT
          $1,
          $2,
          (COUNT(c.id)::NUMERIC / SUM(p.population) * 100000) as nyc_average,
          CURRENT_TIMESTAMP
        FROM nta_population p
        LEFT JOIN crime_complaints c ON c.nta_id = p.nta_id
          AND c.offense_desc = $3
          AND c.complaint_date >= $4
          AND c.complaint_date <= $5
        WHERE p.population > 0
      `, [window, metricKey, offenseDesc, dateRange.start, dateRange.end]);
    }
  }
}

/**
 * Get date range for time window
 */
function getDateRangeForWindow(window) {
  const now = new Date();
  let startDate;

  switch (window) {
    case '12m':
      startDate = new Date(now);
      startDate.setMonth(now.getMonth() - 12);
      break;
    case '24m':
      startDate = new Date(now);
      startDate.setMonth(now.getMonth() - 24);
      break;
    case 'ytd':
      startDate = new Date(now.getFullYear(), 0, 1);
      break;
    default:
      startDate = new Date(now);
      startDate.setMonth(now.getMonth() - 12);
  }

  return {
    start: formatDate(startDate),
    end: formatDate(now)
  };
}

/**
 * Format date for SQL
 */
function formatDate(date) {
  return date.toISOString().split('T')[0];
}

/**
 * Start a pipeline run
 */
async function startPipelineRun(type) {
  const result = await db.query(`
    INSERT INTO pipeline_metadata (run_type, status)
    VALUES ($1, 'running')
    RETURNING id
  `, [type]);

  return result.rows[0].id;
}

/**
 * Complete a pipeline run
 */
async function completePipelineRun(runId, recordsAdded) {
  await db.query(`
    UPDATE pipeline_metadata
    SET status = 'completed',
        records_added = $1,
        completed_at = CURRENT_TIMESTAMP
    WHERE id = $2
  `, [recordsAdded, runId]);
}

/**
 * Fail a pipeline run
 */
async function failPipelineRun(runId, errorMessage) {
  await db.query(`
    UPDATE pipeline_metadata
    SET status = 'failed',
        error_message = $1,
        completed_at = CURRENT_TIMESTAMP
    WHERE id = $2
  `, [errorMessage, runId]);
}

// Run pipeline if called directly
if (require.main === module) {
  runPipeline();
}

module.exports = { runPipeline };
