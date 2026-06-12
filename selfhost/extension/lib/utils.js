/**
 * SleepEasy crime-stats assembly.
 *
 * Takes a coordinate, resolves the NTA, and builds the full payload the crime
 * module renders: per-metric values for every measure, NYC ranks, and
 * city-wide reference values. All lookups are client-side against the bundled
 * data files (see geo-utils.js).
 */

const CONFIG = {
  DEFAULT_TIME_WINDOW: '24m',
  DEFAULT_MEASURE: 'ambient'
};

const MEASURE_DEFS = {
  ambient: { id: 'ambient', label: 'Ambient risk index', unit: '/ 100k present' },
  per100k: { id: 'per100k', label: 'Per 100k residents', unit: '/ 100k residents' },
  perSqMi: { id: 'perSqMi', label: 'Per square mile', unit: '/ sq mi' },
  count: { id: 'count', label: 'Raw incidents', unit: 'incidents' }
};

const TIME_WINDOW_DEFS = {
  '3m': { id: '3m', label: 'Last 3 months' },
  '12m': { id: '12m', label: 'Last 12 months' },
  '24m': { id: '24m', label: 'Last 24 months' }
};

/**
 * Average people present in an NTA, approximated with a simple day/night
 * split: residents counted 16h/day, workers 8h/day.
 */
function computeAmbientPopulation(population, jobsWac) {
  const pop = Number(population) || 0;
  const jobs = Number(jobsWac) || 0;
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
 * City-wide reference value per metric and measure, computed from totals
 * (population-weighted), not as an average of NTA rates. Used for the
 * "x.x× NYC" comparison. Returns null for 'count' (a citywide incident
 * count is not a comparable reference for one neighborhood).
 */
function computeCityValue({ measure, metricKey, windowStats, boundaries, exposures }) {
  if (measure === 'count') return null;

  let totalCount = 0;
  let totalPop = 0;
  let totalAmbient = 0;
  let totalArea = 0;

  for (const [id, ntaMetrics] of Object.entries(windowStats)) {
    const entry = ntaMetrics?.[metricKey];
    if (!entry) continue;
    const count = Number(entry.count);
    if (!Number.isFinite(count)) continue;
    totalCount += count;

    const exposure = exposures?.[id];
    if (exposure) {
      totalPop += Number(exposure.population) || 0;
      totalAmbient += computeAmbientPopulation(exposure.population, exposure.jobsWac);
    }
    const area = Number(boundaries?.[id]?.areaSqMi);
    if (Number.isFinite(area) && area > 0) totalArea += area;
  }

  if (measure === 'per100k') {
    return totalPop > 0 ? (totalCount / totalPop) * 100000 : null;
  }
  if (measure === 'ambient') {
    return totalAmbient > 0 ? (totalCount / totalAmbient) * 100000 : null;
  }
  if (measure === 'perSqMi') {
    return totalArea > 0 ? totalCount / totalArea : null;
  }
  return null;
}

/**
 * Fetch crime statistics for a coordinate from the bundled data.
 * @returns {Promise<Object|null>} payload for the crime module, or null
 */
async function fetchCrimeStats(lat, lon, timeWindow = CONFIG.DEFAULT_TIME_WINDOW) {
  try {
    const nta = await ntaLookup.findNTA(lat, lon);
    if (!nta) {
      SleepEasyLog.warn('[SleepEasy] Location not found in NYC boundaries');
      return null;
    }

    const stats = await crimeStatsManager.getStats(nta.id, timeWindow);
    if (!stats) {
      SleepEasyLog.warn('[SleepEasy] No crime statistics for NTA:', nta.id);
      return null;
    }

    // Exposure powers the ambient measure; if it fails to load those values
    // render as "—" but everything else still works.
    await ntaExposureManager.load();

    const { metrics, city } = enrichMetricsWithMeasures({
      ntaId: nta.id,
      timeWindow,
      metrics: stats.metrics
    });

    return {
      geography: { ntaId: nta.id, ntaName: nta.name, borough: nta.borough },
      metrics,
      city,
      timeWindow,
      dataGenerated: crimeStatsManager?.data?.generated || null,
      dataThrough: stats.dataThrough,
      computedAt: new Date().toISOString(),
      comparisons: (await crimeStatsManager.getComparisons(timeWindow, nta.borough)) || { nycAverage: {} },
      methodologyVersion: stats.methodologyVersion,
      measureDefs: MEASURE_DEFS,
      timeWindowDefs: TIME_WINDOW_DEFS
    };
  } catch (e) {
    SleepEasyLog.error('[SleepEasy] Error fetching crime stats:', e);
    return null;
  }
}

/**
 * Attach computed measures (count/per100k/perSqMi/ambient) with NYC risk
 * ranks to each metric, plus city-wide reference values.
 *
 * NYC rank is ascending: 1 = lowest value = lowest risk. Ties share a rank
 * (competition ranking).
 *
 * @returns {{metrics: Object, city: Object}}
 */
function enrichMetricsWithMeasures({ ntaId, timeWindow, metrics }) {
  const windowStats = crimeStatsManager?.data?.stats?.[timeWindow];
  const boundaries = ntaLookup?.boundaries;
  const exposures = ntaExposureManager?.exposures;
  if (!windowStats || !boundaries || !metrics) return { metrics, city: {} };

  const result = {};
  const city = {};

  for (const [metricKey, metric] of Object.entries(metrics)) {
    const ranksByMeasure = {};
    city[metricKey] = {};

    for (const measure of Object.keys(MEASURE_DEFS)) {
      const items = [];
      for (const [id, m] of Object.entries(windowStats)) {
        const entry = m?.[metricKey];
        if (!entry) continue;
        const value = computeMeasureValue({
          measure,
          entry,
          exposure: exposures?.[id] || null,
          areaSqMi: boundaries[id]?.areaSqMi
        });
        if (!Number.isFinite(value)) continue;
        items.push({ id, value });
      }

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

      ranksByMeasure[measure] = {
        riskRank: ranks[ntaId] ?? null,
        total: total || null
      };

      city[metricKey][measure] = computeCityValue({
        measure, metricKey, windowStats, boundaries, exposures
      });
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

    result[metricKey] = { ...metric, measures };
  }

  return { metrics: result, city };
}

// ── Formatting helpers ──

function formatNumber(num) {
  if (num === null || num === undefined) return '—';
  return Number(num).toLocaleString('en-US');
}

function formatRate(rate) {
  if (rate === null || rate === undefined) return '—';
  const n = Number(rate);
  if (n >= 1000) return Math.round(n).toLocaleString('en-US');
  return n.toFixed(1);
}

/**
 * "0.4×" / "1.2×" / "12×" multiplier vs the city reference value.
 * Returns null when either side is missing or the reference is zero.
 */
function formatCityMultiple(value, cityValue) {
  const v = Number(value);
  const c = Number(cityValue);
  if (!Number.isFinite(v) || !Number.isFinite(c) || c <= 0) return null;
  const ratio = v / c;
  if (ratio >= 10) return `${Math.round(ratio)}×`;
  return `${ratio.toFixed(1)}×`;
}
