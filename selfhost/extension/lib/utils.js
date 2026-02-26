/**
 * SleepEasy Utility Functions
 * Shared utilities for coordinate extraction, geocoding, and data fetching
 *
 * Note: This version uses static precompiled data for crime statistics
 * instead of a backend API. All data lookups happen client-side.
 */

// Configuration
const CONFIG = {
  CACHE_TTL_MS: 1000 * 60 * 60 * 24, // 24 hours
  DEFAULT_TIME_WINDOW: '24m',
  DEFAULT_MEASURE: 'ambient'
};

const MEASURE_DEFS = {
  count: { id: 'count', label: 'Raw', unit: 'incidents' },
  per100k: { id: 'per100k', label: 'Per 100k', unit: '/ 100k' },
  perSqMi: { id: 'perSqMi', label: 'Per sq mi', unit: '/ sq mi' },
  ambient: { id: 'ambient', label: 'Ambient', unit: '/ 100k' }
};

function computeAmbientPopulation(population, jobsWac) {
  const pop = Number(population) || 0;
  const jobs = Number(jobsWac) || 0;
  // Approximate average people present (person-hours) using a simple day/night split.
  // 16h residents + 8h workers.
  return ((pop * 16) + (jobs * 8)) / 24;
}

function computeMeasureValue({ measure, entry, exposure, areaSqMi }) {
  if (!entry) return null;
  const count = Number(entry.count);

  if (measure === 'count') return Number.isFinite(count) ? count : null;
  if (measure === 'per100k') {
    const rate = Number(entry.rate);
    return Number.isFinite(rate) ? rate : null;
  }

  if (measure === 'perSqMi') {
    const area = Number(areaSqMi);
    if (!Number.isFinite(count) || !Number.isFinite(area) || area <= 0) return null;
    return count / area;
  }

  if (measure === 'ambient') {
    const ambientPop = computeAmbientPopulation(exposure?.population, exposure?.jobsWac);
    if (!Number.isFinite(count) || !Number.isFinite(ambientPop) || ambientPop <= 0) return null;
    return (count / ambientPop) * 100000;
  }

  return null;
}

/**
 * Fetch crime statistics using static precompiled data (no backend required)
 * @param {number} lat - Latitude
 * @param {number} lon - Longitude
 * @param {string} window - Time window (e.g., '12m', '24m', 'ytd')
 * @returns {Promise<Object|null>} Crime statistics data
 */
async function fetchCrimeStats(lat, lon, window = CONFIG.DEFAULT_TIME_WINDOW) {
  try {
    // Find NTA for this location using client-side point-in-polygon
    const nta = await ntaLookup.findNTA(lat, lon);

    if (!nta) {
      SleepEasyLog.warn('[SleepEasy] Location not found in NYC boundaries');
      return null;
    }

    // Get crime statistics from precompiled data
    const stats = await crimeStatsManager.getStats(nta.id, window);

    if (!stats) {
      SleepEasyLog.warn('[SleepEasy] No crime statistics for NTA:', nta.id);
      return null;
    }

    const dataGenerated = crimeStatsManager?.data?.generated || null;

    // Get comparisons with borough-specific data
    const comparisons = await crimeStatsManager.getComparisons(window, nta.borough);

    const exposureOk = await (ntaExposureManager?.load?.() ?? Promise.resolve(false));
    if (!exposureOk) {
      SleepEasyLog.warn('[SleepEasy] Exposure data unavailable; per-area/ambient measures will be unavailable');
    }

    const enrichedMetrics = enrichMetricsWithMeasures({
      ntaId: nta.id,
      timeWindow: window,
      metrics: stats.metrics
    });

    // Build response matching the original API format
    return {
      geography: {
        ntaId: nta.id,
        ntaName: nta.name,
        borough: nta.borough
      },
      metrics: enrichedMetrics,
      timeWindow: window,
      dataGenerated,
      dataThrough: stats.dataThrough,
      computedAt: new Date().toISOString(),
      comparisons: comparisons || { nycAverage: {}, boroughAverage: {} },
      methodologyVersion: stats.methodologyVersion,
      measureDefs: MEASURE_DEFS
    };

  } catch (e) {
    SleepEasyLog.error('[SleepEasy] Error fetching crime stats:', e);
    return null;
  }
}

/**
 * Attach computed measures (count/rate/density/ambient) and NYC risk ranks.
 * NYC rank is ascending: 1 = lowest risk (lowest value).
 * @param {{ntaId: string, timeWindow: string, metrics: Object}} params
 * @returns {Object}
 */
function enrichMetricsWithMeasures({ ntaId, timeWindow, metrics }) {
  const windowStats = crimeStatsManager?.data?.stats?.[timeWindow];
  const boundaries = ntaLookup?.boundaries;
  const exposures = ntaExposureManager?.exposures;
  if (!windowStats || !boundaries || !metrics) return metrics;

  const result = {};

  for (const [metricKey, metric] of Object.entries(metrics)) {
    const ranksByMeasure = {};

    for (const measure of Object.keys(MEASURE_DEFS)) {
      const items = [];
      for (const [id, m] of Object.entries(windowStats)) {
        const entry = m?.[metricKey];
        if (!entry) continue;
        const areaSqMi = boundaries[id]?.areaSqMi;
        const exposure = exposures?.[id] || null;
        const value = computeMeasureValue({ measure, entry, exposure, areaSqMi });
        if (!Number.isFinite(value)) continue;
        items.push({ id, value });
      }

      // NYC rank: 1 = lowest risk (lowest value).
      // Use a tie-aware competition rank so identical values share the same rank.
      items.sort((a, b) => (a.value - b.value) || a.id.localeCompare(b.id));

      const total = items.length;
      const ranks = {};
      let currentRank = 0;
      let lastValue = null;
      for (let i = 0; i < items.length; i++) {
        const v = items[i].value;
        if (i === 0 || v !== lastValue) {
          currentRank = i + 1;
          lastValue = v;
        }
        ranks[items[i].id] = currentRank;
      }

      const rank = ranks[ntaId] ?? null;
      ranksByMeasure[measure] = {
        riskRank: rank,
        total: total || null
      };
    }

    const areaSqMi = boundaries[ntaId]?.areaSqMi;
    const exposure = exposures?.[ntaId] || null;

    const measures = {};
    for (const measure of Object.keys(MEASURE_DEFS)) {
      const value = computeMeasureValue({ measure, entry: metric, exposure, areaSqMi });
      measures[measure] = {
        value: Number.isFinite(value) ? value : null,
        riskRank: ranksByMeasure[measure]?.riskRank ?? null,
        total: ranksByMeasure[measure]?.total ?? null
      };
    }

    result[metricKey] = {
      ...metric,
      measures
    };
  }

  return result;
}

/**
 * Format a number with commas
 * @param {number} num - Number to format
 * @returns {string} Formatted number
 */
function formatNumber(num) {
  if (num === null || num === undefined) return '—';
  return Number(num).toLocaleString('en-US');
}

/**
 * Format a rate per 100k
 * @param {number} rate - Rate to format
 * @returns {string} Formatted rate
 */
function formatRate(rate) {
  if (rate === null || rate === undefined) return '—';
  return Number(rate).toFixed(1);
}

/**
 * Format a percentile for display
 * @param {number} percentile - Percentile (0-100)
 * @returns {string} Display text
 */
function formatPercentile(percentile) {
  const rounded = Math.round(percentile);
  return `Safer than ${rounded}% of NYC neighborhoods`;
}

/**
 * Get color class based on percentile
 * @param {number} percentile - Percentile (0-100)
 * @returns {string} CSS class name
 */
function getPercentileColorClass(percentile) {
  if (percentile >= 75) return 'percentile-high'; // Green - Very safe
  if (percentile >= 50) return 'percentile-medium'; // Yellow - Average
  if (percentile >= 25) return 'percentile-low'; // Orange - Below average
  return 'percentile-very-low'; // Red - Low safety
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    CONFIG,
    fetchCrimeStats,
    formatNumber,
    formatRate,
    formatPercentile,
    getPercentileColorClass
  };
}
