/**
 * SleepEasy Backend API
 * Serves crime statistics for NYC locations
 */

const express = require('express');
const cors = require('cors');
const NodeCache = require('node-cache');
require('dotenv').config();

const db = require('../db/connection');

const app = express();
const PORT = process.env.PORT || 3000;

// Cache for 1 hour by default
const cache = new NodeCache({ stdTTL: 3600 });

// Middleware
app.use(cors());
app.use(express.json());

// Request logging
app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
  next();
});

/**
 * Health check endpoint
 */
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

/**
 * Get crime statistics for a location
 * GET /v1/safety?lat=40.7&lon=-74.0&window=12m
 */
app.get('/v1/safety', async (req, res) => {
  try {
    const { lat, lon, window = '12m' } = req.query;

    // Validate parameters
    if (!lat || !lon) {
      return res.status(400).json({
        error: 'Missing required parameters: lat and lon'
      });
    }

    const latitude = parseFloat(lat);
    const longitude = parseFloat(lon);

    if (isNaN(latitude) || isNaN(longitude)) {
      return res.status(400).json({
        error: 'Invalid coordinates'
      });
    }

    // Check cache
    const cacheKey = `stats_${latitude}_${longitude}_${window}`;
    const cached = cache.get(cacheKey);
    if (cached) {
      console.log('Cache hit:', cacheKey);
      return res.json(cached);
    }

    // Find NTA for this location
    const nta = await findNTAForLocation(latitude, longitude);
    if (!nta) {
      return res.status(404).json({
        error: 'Location not found in NYC boundaries'
      });
    }

    // Get crime statistics
    const stats = await getCrimeStatistics(nta.nta_id, window);
    if (!stats) {
      return res.status(500).json({
        error: 'Error fetching crime statistics'
      });
    }

    // Build response
    const response = {
      geography: {
        ntaId: nta.nta_id,
        ntaName: nta.nta_name,
        borough: nta.borough
      },
      metrics: stats.metrics,
      timeWindow: window,
      dataThrough: stats.data_through,
      computedAt: new Date().toISOString(),
      comparisons: stats.comparisons,
      methodologyVersion: '1.0.0'
    };

    // Cache response
    cache.set(cacheKey, response);

    res.json(response);

  } catch (error) {
    console.error('Error in /v1/safety:', error);
    res.status(500).json({
      error: 'Internal server error'
    });
  }
});

/**
 * Find NTA for a given lat/lon
 */
async function findNTAForLocation(lat, lon) {
  try {
    const result = await db.query(`
      SELECT
        nta_id,
        nta_name,
        borough
      FROM nta_boundaries
      WHERE ST_Contains(
        geom,
        ST_SetSRID(ST_MakePoint($1, $2), 4326)
      )
      LIMIT 1
    `, [lon, lat]);

    return result.rows[0] || null;
  } catch (error) {
    console.error('Error finding NTA:', error);
    return null;
  }
}

/**
 * Get crime statistics for an NTA
 */
async function getCrimeStatistics(ntaId, window) {
  try {
    // Calculate date range based on window
    const dateRange = getDateRangeForWindow(window);

    // Get metrics
    const metricsResult = await db.query(`
      SELECT
        metric_type,
        count,
        rate,
        percentile,
        rank,
        total_neighborhoods
      FROM crime_metrics
      WHERE nta_id = $1
        AND time_window = $2
        AND computed_at = (
          SELECT MAX(computed_at)
          FROM crime_metrics
          WHERE nta_id = $1 AND time_window = $2
        )
    `, [ntaId, window]);

    if (metricsResult.rows.length === 0) {
      return null;
    }

    // Transform metrics into object
    const metrics = {};
    metricsResult.rows.forEach(row => {
      metrics[row.metric_type] = {
        count: row.count,
        rate: parseFloat(row.rate),
        percentile: parseFloat(row.percentile),
        rank: row.rank,
        total: row.total_neighborhoods
      };
    });

    // Get comparisons (NYC and borough averages)
    const comparisonsResult = await db.query(`
      SELECT
        metric_type,
        nyc_average,
        borough_average
      FROM crime_comparisons
      WHERE time_window = $1
        AND computed_at = (
          SELECT MAX(computed_at)
          FROM crime_comparisons
          WHERE time_window = $1
        )
    `, [window]);

    const comparisons = {
      nycAverage: {},
      boroughAverage: {}
    };

    comparisonsResult.rows.forEach(row => {
      comparisons.nycAverage[row.metric_type] = parseFloat(row.nyc_average);
      // Note: Borough average would need the specific borough
    });

    // Get data freshness
    const freshnessResult = await db.query(`
      SELECT MAX(complaint_date) as data_through
      FROM crime_complaints
    `);

    return {
      metrics,
      comparisons,
      data_through: freshnessResult.rows[0]?.data_through || new Date().toISOString()
    };

  } catch (error) {
    console.error('Error getting crime statistics:', error);
    return null;
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
    start: startDate.toISOString().split('T')[0],
    end: now.toISOString().split('T')[0]
  };
}

// Start server
app.listen(PORT, () => {
  console.log(`[SleepEasy API] Server running on port ${PORT}`);
  console.log(`[SleepEasy API] Environment: ${process.env.NODE_ENV || 'development'}`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM received, shutting down gracefully');
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('SIGINT received, shutting down gracefully');
  process.exit(0);
});
