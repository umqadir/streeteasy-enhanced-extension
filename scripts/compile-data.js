#!/usr/bin/env node

/**
 * SleepEasy Data Compiler
 *
 * Fetches data from NYC Open Data and compiles static JSON files for the extension.
 * This script should be run periodically (e.g., weekly) to update the crime statistics.
 *
 * Usage:
 *   node scripts/compile-data.js
 *
 * Output:
 *   extension/data/nta-boundaries.json - NTA polygon boundaries for point-in-polygon lookup
 *   extension/data/crime-stats.json - Precomputed crime statistics per NTA
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// 2020 Decennial Census population by NTA (from Census Bureau P1 table)
// Source: Census API 2020/dec/pl, aggregated via tract-to-NTA crosswalk
const NTA_POPULATIONS_2020 = {
  "BK0101": 38980, "BK0102": 64444, "BK0103": 47703, "BK0104": 52998,
  "BK0201": 25092, "BK0202": 40968, "BK0203": 32938, "BK0204": 28647,
  "BK0301": 89189, "BK0302": 84653, "BK0401": 58692, "BK0402": 62049,
  "BK0501": 43256, "BK0502": 42818, "BK0503": 53004, "BK0504": 17038,
  "BK0505": 45559, "BK0601": 59166, "BK0602": 57405, "BK0701": 23622,
  "BK0702": 54473, "BK0703": 55606, "BK0801": 23515, "BK0802": 85275,
  "BK0901": 50590, "BK0902": 49513, "BK1001": 86779, "BK1002": 46756,
  "BK1101": 104934, "BK1102": 33070, "BK1103": 60523, "BK1201": 35632,
  "BK1202": 93905, "BK1203": 41758, "BK1204": 39137, "BK1301": 30366,
  "BK1302": 49517, "BK1303": 33568, "BK1401": 66503, "BK1402": 43016,
  "BK1403": 52891, "BK1501": 52797, "BK1502": 41465, "BK1503": 69084,
  "BK1601": 37952, "BK1602": 60470, "BK1701": 47690, "BK1702": 34317,
  "BK1703": 42762, "BK1704": 39115, "BK1801": 67087, "BK1802": 46955,
  "BK1803": 89932, "BX0101": 57718, "BX0102": 42651, "BX0201": 15131,
  "BX0202": 40289, "BX0301": 37607, "BX0302": 24553, "BX0303": 30158,
  "BX0401": 69387, "BX0402": 32456, "BX0403": 49651, "BX0501": 54682,
  "BX0502": 49099, "BX0503": 32099, "BX0601": 20147, "BX0602": 32150,
  "BX0603": 35825, "BX0701": 46538, "BX0702": 55521, "BX0703": 43466,
  "BX0801": 35777, "BX0802": 8594, "BX0803": 47927, "BX0901": 74342,
  "BX0902": 37297, "BX0903": 41420, "BX0904": 33602, "BX1001": 17376,
  "BX1002": 48116, "BX1003": 29462, "BX1004": 37369, "BX1101": 32425,
  "BX1102": 25077, "BX1103": 29402, "BX1104": 34623, "BX1201": 61346,
  "BX1202": 51736, "BX1203": 47657, "MN0101": 52992, "MN0102": 25390,
  "MN0201": 23287, "MN0202": 34147, "MN0203": 35011, "MN0301": 42556,
  "MN0302": 49149, "MN0303": 71436, "MN0401": 69741, "MN0402": 59524,
  "MN0501": 33214, "MN0502": 25575, "MN0601": 22512, "MN0602": 28628,
  "MN0603": 63520, "MN0604": 45765, "MN0701": 70697, "MN0702": 101767,
  "MN0703": 51751, "MN0801": 85527, "MN0802": 62410, "MN0803": 84046,
  "MN0901": 38865, "MN0902": 22183, "MN0903": 49410, "MN1001": 47113,
  "MN1002": 83327, "MN1101": 59814, "MN1102": 64655, "MN1201": 72037,
  "MN1202": 71842, "MN1203": 36299, "QN0101": 50225, "QN0102": 18927,
  "QN0103": 52220, "QN0104": 38673, "QN0105": 32954, "QN0151": 3772,
  "QN0201": 32216, "QN0202": 52278, "QN0203": 53188, "QN0301": 101848,
  "QN0302": 33573, "QN0303": 43434, "QN0401": 107864, "QN0402": 73822,
  "QN0501": 43257, "QN0502": 66402, "QN0503": 35135, "QN0504": 34474,
  "QN0601": 30741, "QN0602": 88965, "QN0701": 33625, "QN0702": 28408,
  "QN0703": 23287, "QN0704": 57387, "QN0705": 34704, "QN0706": 23638,
  "QN0707": 69879, "QN0801": 36009, "QN0802": 35669, "QN0803": 23434,
  "QN0804": 23576, "QN0805": 39292, "QN0901": 24371, "QN0902": 34100,
  "QN0903": 24141, "QN0904": 27312, "QN0905": 41948, "QN1001": 79540,
  "QN1002": 23518, "QN1003": 27320, "QN1101": 36182, "QN1102": 35588,
  "QN1103": 25262, "QN1104": 25249, "QN1201": 60993, "QN1202": 44401,
  "QN1203": 43090, "QN1204": 32883, "QN1205": 51816, "QN1206": 24576,
  "QN1301": 23364, "QN1302": 26566, "QN1303": 54345, "QN1304": 19081,
  "QN1305": 26088, "QN1306": 23103, "QN1307": 27101, "QN1401": 58648,
  "QN1402": 41367, "QN1403": 24134, "SI0101": 20549, "SI0102": 19027,
  "SI0103": 25510, "SI0104": 37010, "SI0105": 31458, "SI0106": 22609,
  "SI0107": 33492, "SI0201": 36259, "SI0202": 29083, "SI0203": 32822,
  "SI0204": 42871, "SI0301": 22388, "SI0302": 54699, "SI0303": 30683,
  "SI0304": 40534, "SI0305": 16089
};

// Configuration
const CONFIG = {
  // NYC Open Data endpoints (using 2020 NTA boundaries)
  NTA_BOUNDARIES_URL: 'https://data.cityofnewyork.us/resource/9nt8-h7nd.json',

  // NYPD complaint data is split across two datasets:
  // - Historic (all prior years, typically updated with a lag)
  // - Current year to date
  // For accurate rolling windows (e.g., 24 months), we merge both and dedupe by `cmplnt_num`.
  CRIME_DATA_HISTORIC_URL: 'https://data.cityofnewyork.us/resource/qgea-i56i.json',
  CRIME_DATA_CURRENT_URL: 'https://data.cityofnewyork.us/resource/5uac-w243.json',

  // Output paths
  OUTPUT_DIR: path.join(__dirname, '..', 'extension', 'data'),

  // Crime categories to track
  CRIME_CATEGORIES: {
    murder: ['MURDER & NON-NEGL. MANSLAUGHTER'],
    felonyAssault: ['FELONY ASSAULT'],
    // Property crime index (felony-level property offenses)
    propertyCrime: ['BURGLARY', 'GRAND LARCENY', 'GRAND LARCENY OF MOTOR VEHICLE']
  },

  // Time windows to compute
  TIME_WINDOWS: ['3m', '6m', '12m', '24m', 'ytd'],

  // Fallback population if NTA not in census data
  DEFAULT_POPULATION: 40000
};

/**
 * Make an HTTPS GET request using curl (handles proxy automatically)
 */
function fetchJSON(url) {
  console.log(`Fetching: ${url.substring(0, 100)}...`);

  try {
    // Escape the URL for shell and use single quotes to prevent variable expansion
    const escapedUrl = url.replace(/'/g, "'\\''");
    const result = execSync(
      `curl -fsS --compressed --retry 3 --retry-delay 1 --max-time 120 '${escapedUrl}'`,
      {
      encoding: 'utf8',
      maxBuffer: 100 * 1024 * 1024 // 100MB buffer
      }
    );

    const parsed = JSON.parse(result);
    if (!Array.isArray(parsed)) {
      console.log('  Response:', typeof parsed, JSON.stringify(parsed).substring(0, 200));
    }
    return parsed;
  } catch (e) {
    const stderr = e.stderr ? String(e.stderr) : '';
    const stdout = e.stdout ? String(e.stdout) : '';
    if (stderr.trim()) console.error('  Curl stderr:', stderr.trim().substring(0, 500));
    if (stdout.trim()) console.error('  Curl stdout:', stdout.trim().substring(0, 500));
    throw new Error(`Failed to fetch ${url}: ${e.message}`);
  }
}

function sleepMs(ms) {
  const shared = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(shared, 0, 0, ms);
}

function toISODate(value) {
  if (!value) return null;
  const str = String(value);
  // Common format: 2025-01-31T00:00:00.000
  if (str.length >= 10 && /^\d{4}-\d{2}-\d{2}/.test(str)) return str.slice(0, 10);
  try {
    const d = new Date(str);
    if (Number.isNaN(d.getTime())) return null;
    return d.toISOString().slice(0, 10);
  } catch {
    return null;
  }
}

/**
 * Fetch NTA boundaries from NYC Open Data
 */
async function fetchNTABoundaries() {
  console.log('\n[1/4] Fetching NTA boundaries...');

  // Fetch NTA data with geometry - using 2020 NTAs
  const url = `${CONFIG.NTA_BOUNDARIES_URL}?$limit=500`;
  const data = fetchJSON(url);

  console.log(`  Fetched ${data.length} NTA records`);

  // Transform to simplified format
  const boundaries = {};

  for (const nta of data) {
    const ntaCode = nta.nta2020 || nta.ntacode;
    const ntaName = nta.ntaname;
    const borough = nta.boroname;
    const geometry = nta.the_geom;

    if (!ntaCode || !geometry) continue;

    // Skip non-residential NTAs (parks, airports, cemeteries, etc.)
    // NTA type 0 = residential, others are parks/airports/cemeteries
    if (nta.ntatype && nta.ntatype !== '0') {
      console.log(`  Skipping non-residential NTA: ${ntaCode} (${ntaName})`);
      continue;
    }

    try {
      boundaries[ntaCode] = {
        id: ntaCode,
        name: ntaName || ntaCode,
        borough: borough || 'Unknown',
        geometry: geometry
      };
    } catch (e) {
      console.warn(`  Warning: Could not process ${ntaCode}: ${e.message}`);
    }
  }

  console.log(`  Processed ${Object.keys(boundaries).length} residential NTAs`);
  return boundaries;
}

/**
 * Fetch crime complaint data for a date range
 */
function fetchCrimeDataFromDataset(datasetUrl, startDate, endDate, datasetName = 'dataset') {
  console.log(`  Fetching ${datasetName} crimes from ${startDate} to ${endDate}...`);

  // SoQL query for crime data
  const baseWhere = `${getOffenseWhereClause()} AND cmplnt_num IS NOT NULL AND cmplnt_fr_dt >= '${startDate}' AND cmplnt_fr_dt <= '${endDate}'`;

  const LIMIT = 50000;
  let lastComplaintNum = null;
  const all = [];

  while (true) {
    const lastEscaped = lastComplaintNum ? String(lastComplaintNum).replace(/'/g, "''") : null;
    const where = lastEscaped ? `${baseWhere} AND cmplnt_num > '${lastEscaped}'` : baseWhere;

    const params = new URLSearchParams({
      '$where': where,
      '$limit': String(LIMIT),
      '$order': 'cmplnt_num',
      '$select': 'cmplnt_num,cmplnt_fr_dt,ofns_desc,latitude,longitude'
    });

    const url = `${datasetUrl}?${params.toString()}`;
    const page = fetchJSON(url);
    all.push(...page);

    if (page.length === 0) break;
    if (page.length < LIMIT) break;

    const lastRow = page[page.length - 1];
    const parsed = lastRow?.cmplnt_num ? String(lastRow.cmplnt_num) : null;
    if (!parsed) {
      console.warn(`  Warning: ${datasetName} pagination stopped (invalid last cmplnt_num)`);
      break;
    }
    if (lastComplaintNum !== null && parsed <= lastComplaintNum) {
      console.warn(`  Warning: ${datasetName} pagination stopped (cmplnt_num did not advance)`);
      break;
    }

    lastComplaintNum = parsed;

    // Be a little gentle with Socrata rate limits.
    sleepMs(200);
  }

  console.log(`  Fetched ${all.length} crime complaints`);
  return all;
}

function mergeAndDedupeCrimes(datasets) {
  const seen = new Map();
  let missingId = 0;

  for (const { name, rows } of datasets) {
    for (const row of rows) {
      const idRaw = row?.cmplnt_num ?? null;
      const id = idRaw ? String(idRaw) : null;
      if (!id) {
        missingId += 1;
        continue;
      }
      if (!seen.has(id)) {
        seen.set(id, { ...row, __source: name });
      }
    }
  }

  return { crimes: Array.from(seen.values()), missingId };
}

function computeDataThrough(crimes) {
  let max = null;
  for (const c of crimes) {
    const d = toISODate(c?.cmplnt_fr_dt);
    if (!d) continue;
    if (!max || d > max) max = d;
  }
  return max;
}

function getOffenseWhereClause() {
  const offenses = Object.values(CONFIG.CRIME_CATEGORIES).flat();
  const offenseFilter = `upper(ofns_desc) in(${offenses.map(s => `'${String(s).toUpperCase().replace(/'/g, "''")}'`).join(',')})`;
  return `cmplnt_fr_dt IS NOT NULL AND (${offenseFilter}) AND latitude IS NOT NULL AND longitude IS NOT NULL`;
}

function fetchMaxComplaintDate(datasetUrl, datasetName) {
  console.log(`  Fetching ${datasetName} max complaint date...`);

  const where = getOffenseWhereClause();
  const params = new URLSearchParams({
    '$select': 'max(cmplnt_fr_dt)',
    '$where': where,
    '$limit': '1'
  });

  const url = `${datasetUrl}?${params.toString()}`;
  const rows = fetchJSON(url);
  const raw = rows?.[0]?.max_cmplnt_fr_dt ?? null;
  const iso = toISODate(raw);
  if (!iso) {
    console.warn(`  Warning: Could not determine max complaint date for ${datasetName}`);
  } else {
    console.log(`  ${datasetName} data through: ${iso}`);
  }
  return iso;
}

function fetchWindowEndDate() {
  console.log('\nDetermining latest available date...');

  const historic = fetchMaxComplaintDate(CONFIG.CRIME_DATA_HISTORIC_URL, 'historic');
  sleepMs(200);
  const current = fetchMaxComplaintDate(CONFIG.CRIME_DATA_CURRENT_URL, 'current');

  const endDate = [historic, current].filter(Boolean).sort().pop() || null;
  if (!endDate) {
    console.warn('  Warning: Falling back to today for date windows');
    return new Date().toISOString().split('T')[0];
  }

  console.log(`  Using end date: ${endDate}`);
  return endDate;
}

/**
 * Fetch crime complaint data for a date range (merged historic + current).
 */
function fetchCrimeData(startDate, endDate) {
  const historic = fetchCrimeDataFromDataset(CONFIG.CRIME_DATA_HISTORIC_URL, startDate, endDate, 'historic');
  // Be gentle with Socrata.
  sleepMs(250);
  const current = fetchCrimeDataFromDataset(CONFIG.CRIME_DATA_CURRENT_URL, startDate, endDate, 'current');

  const { crimes, missingId } = mergeAndDedupeCrimes([
    { name: 'historic', rows: historic },
    { name: 'current', rows: current }
  ]);

  const dataThrough = computeDataThrough(crimes);

  console.log(`  Combined unique complaints: ${crimes.length} (deduped by cmplnt_num)`);
  if (missingId) console.log(`  Warning: ${missingId} records missing cmplnt_num were skipped`);
  if (dataThrough) console.log(`  Data through: ${dataThrough}`);

  return { crimes, dataThrough };
}

/**
 * Simple point-in-polygon test using ray casting algorithm
 */
function pointInPolygon(point, polygon) {
  const [x, y] = point;
  let inside = false;

  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];

    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }

  return inside;
}

/**
 * Check if a point is in any ring of a polygon/multipolygon
 */
function pointInGeometry(lon, lat, geometry) {
  const point = [lon, lat];

  if (geometry.type === 'Polygon') {
    if (!pointInPolygon(point, geometry.coordinates[0])) return false;
    for (let i = 1; i < geometry.coordinates.length; i++) {
      if (pointInPolygon(point, geometry.coordinates[i])) return false;
    }
    return true;
  } else if (geometry.type === 'MultiPolygon') {
    for (const polygon of geometry.coordinates) {
      if (pointInPolygon(point, polygon[0])) {
        let inHole = false;
        for (let i = 1; i < polygon.length; i++) {
          if (pointInPolygon(point, polygon[i])) {
            inHole = true;
            break;
          }
        }
        if (!inHole) return true;
      }
    }
  }

  return false;
}

function computeGeometryBBox(geometry) {
  if (!geometry) return null;

  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;

  const visitRing = (ring) => {
    for (const coord of ring) {
      if (!coord || coord.length < 2) continue;
      const lon = coord[0];
      const lat = coord[1];
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
  };

  if (geometry.type === 'Polygon') {
    for (const ring of geometry.coordinates || []) visitRing(ring);
  } else if (geometry.type === 'MultiPolygon') {
    for (const poly of geometry.coordinates || []) {
      for (const ring of poly || []) visitRing(ring);
    }
  }

  if (!Number.isFinite(minLon) || !Number.isFinite(minLat) || !Number.isFinite(maxLon) || !Number.isFinite(maxLat)) {
    return null;
  }

  return { minLon, minLat, maxLon, maxLat };
}

/**
 * Assign crime complaints to NTAs based on coordinates
 */
function assignCrimesToNTAs(crimes, boundaries) {
  console.log('\n  Assigning crimes to NTAs...');

  const events = [];
  let assigned = 0;
  let unassigned = 0;

  const metricKeys = Object.keys(CONFIG.CRIME_CATEGORIES);
  const offenseToMetric = {};
  for (const [metricKey, offenses] of Object.entries(CONFIG.CRIME_CATEGORIES)) {
    for (const offense of offenses) {
      offenseToMetric[String(offense).toUpperCase()] = metricKey;
    }
  }

  const ntaIndex = Object.entries(boundaries).map(([ntaId, nta]) => ({
    ntaId,
    geometry: nta.geometry,
    bbox: computeGeometryBBox(nta.geometry)
  }));

  // Assign each crime to an NTA
  for (const crime of crimes) {
    const lon = parseFloat(crime.longitude);
    const lat = parseFloat(crime.latitude);
    const date = toISODate(crime.cmplnt_fr_dt);

    if (isNaN(lon) || isNaN(lat) || !date) {
      unassigned++;
      continue;
    }

    const offense = String(crime.ofns_desc || '').toUpperCase();
    const metricKey = offenseToMetric[offense] || null;
    if (!metricKey) {
      unassigned++;
      continue;
    }

    let foundNTA = null;
    for (const nta of ntaIndex) {
      const bbox = nta.bbox;
      if (bbox) {
        if (lon < bbox.minLon || lon > bbox.maxLon || lat < bbox.minLat || lat > bbox.maxLat) continue;
      }
      if (pointInGeometry(lon, lat, nta.geometry)) {
        foundNTA = nta.ntaId;
        break;
      }
    }

    if (foundNTA) {
      events.push({ ntaId: foundNTA, metricKey, date });
      assigned++;
    } else {
      unassigned++;
    }
  }

  console.log(`  Assigned ${assigned} crimes, ${unassigned} could not be assigned`);
  return { events, assigned, unassigned };
}

function initNTACounts(boundaries) {
  const metricKeys = Object.keys(CONFIG.CRIME_CATEGORIES);
  const result = {};
  for (const ntaId of Object.keys(boundaries)) {
    result[ntaId] = Object.fromEntries(metricKeys.map(k => [k, 0]));
  }
  return result;
}

function computeCountsByWindow(events, boundaries, windowRanges) {
  const metricKeys = Object.keys(CONFIG.CRIME_CATEGORIES);
  if (!Array.isArray(windowRanges) || windowRanges.length === 0) {
    throw new Error('computeCountsByWindow requires windowRanges');
  }
  const byWindow = {};
  for (const r of windowRanges) {
    byWindow[r.window] = initNTACounts(boundaries);
  }

  const firstWindow = windowRanges[0].window;
  for (const event of events) {
    if (!event) continue;
    const ntaId = event.ntaId;
    const metricKey = event.metricKey;
    const date = event.date;
    if (!ntaId || !metricKey || !date) continue;
    if (!byWindow[firstWindow]?.[ntaId]) continue;
    if (!metricKeys.includes(metricKey)) continue;

    for (const r of windowRanges) {
      if (date < r.start || date > r.end) continue;
      byWindow[r.window][ntaId][metricKey] += 1;
    }
  }

  return byWindow;
}

/**
 * Get population for NTAs from 2020 Decennial Census
 * Falls back to DEFAULT_POPULATION for unknown NTAs
 */
function estimatePopulations(boundaries) {
  const populations = {};
  let censusMatches = 0;
  let fallbacks = 0;

  for (const [ntaId, nta] of Object.entries(boundaries)) {
    if (NTA_POPULATIONS_2020[ntaId]) {
      populations[ntaId] = NTA_POPULATIONS_2020[ntaId];
      censusMatches++;
    } else {
      // Fallback for NTAs not in census data (shouldn't happen for residential NTAs)
      populations[ntaId] = CONFIG.DEFAULT_POPULATION;
      fallbacks++;
      console.log(`  Warning: No census population for ${ntaId}, using default`);
    }
  }

  console.log(`  Census data matched: ${censusMatches}, fallbacks: ${fallbacks}`);
  return populations;
}

/**
 * Compute statistics for all NTAs
 */
function computeStatistics(ntaCrimes, boundaries, population) {
  console.log('\n[4/4] Computing statistics...');

  const stats = {};
  const ntaIds = Object.keys(boundaries);

  for (const metricKey of Object.keys(CONFIG.CRIME_CATEGORIES)) {
    const rateData = [];

    for (const ntaId of ntaIds) {
      const count = Number(ntaCrimes[ntaId]?.[metricKey] || 0);
      const pop = population[ntaId] || CONFIG.DEFAULT_POPULATION;
      const rate = pop > 0 ? (count / pop) * 100000 : 0;

      rateData.push({ ntaId, count, rate, population: pop });
    }

    // Sort by rate ascending for ranking (lower rate = better = lower rank)
    rateData.sort((a, b) => a.rate - b.rate);

    const total = rateData.length;
    rateData.forEach((item, index) => {
      const rank = index + 1;
      // Percentile: % of neighborhoods with HIGHER crime rates (higher = safer)
      const percentile = ((total - rank) / total) * 100;

      if (!stats[item.ntaId]) {
        stats[item.ntaId] = {};
      }

      stats[item.ntaId][metricKey] = {
        count: item.count,
        rate: Math.round(item.rate * 100) / 100,
        rank: rank,
        total: total,
        percentile: Math.round(percentile * 10) / 10
      };
    });
  }

  // Compute NYC and borough averages
  const comparisons = computeComparisons(ntaCrimes, boundaries, population);

  console.log(`  Computed statistics for ${Object.keys(stats).length} NTAs`);
  return { stats, comparisons };
}

/**
 * Compute NYC-wide and borough averages
 */
function computeComparisons(ntaCrimes, boundaries, population) {
  const metricKeys = Object.keys(CONFIG.CRIME_CATEGORIES);
  const boroughStats = {};
  const nycStats = { totalPop: 0, counts: Object.fromEntries(metricKeys.map(k => [k, 0])) };

  for (const [ntaId, nta] of Object.entries(boundaries)) {
    const pop = population[ntaId] || CONFIG.DEFAULT_POPULATION;
    const crimes = ntaCrimes[ntaId] || {};

    const borough = nta.borough;
    if (!boroughStats[borough]) {
      boroughStats[borough] = { totalPop: 0, counts: Object.fromEntries(metricKeys.map(k => [k, 0])) };
    }

    boroughStats[borough].totalPop += pop;
    for (const key of metricKeys) {
      boroughStats[borough].counts[key] += Number(crimes[key] || 0);
    }

    nycStats.totalPop += pop;
    for (const key of metricKeys) {
      nycStats.counts[key] += Number(crimes[key] || 0);
    }
  }

  const ratePer100k = (count, pop) => pop > 0 ? Math.round((count / pop) * 100000 * 100) / 100 : 0;

  const comparisons = {
    nycAverage: Object.fromEntries(metricKeys.map(k => [k, ratePer100k(nycStats.counts[k], nycStats.totalPop)])),
    boroughAverage: {}
  };

  for (const [borough, stats] of Object.entries(boroughStats)) {
    comparisons.boroughAverage[borough] = Object.fromEntries(
      metricKeys.map(k => [k, ratePer100k(stats.counts[k], stats.totalPop)])
    );
  }

  return comparisons;
}

/**
 * Get date range for a time window
 */
function getDateRange(window, endDateISO) {
  if (!window || typeof window !== 'string') throw new Error('getDateRange requires a window string');
  if (!endDateISO) throw new Error('getDateRange requires an endDateISO (YYYY-MM-DD)');

  const endDate = new Date(`${endDateISO}T00:00:00Z`);
  let startDate;

  switch (window) {
    case '3m':
      startDate = new Date(endDate);
      startDate.setMonth(endDate.getMonth() - 3);
      break;
    case '6m':
      startDate = new Date(endDate);
      startDate.setMonth(endDate.getMonth() - 6);
      break;
    case '12m':
      startDate = new Date(endDate);
      startDate.setMonth(endDate.getMonth() - 12);
      break;
    case '24m':
      startDate = new Date(endDate);
      startDate.setMonth(endDate.getMonth() - 24);
      break;
    case 'ytd':
      startDate = new Date(endDate.getUTCFullYear(), 0, 1);
      break;
    default:
      startDate = new Date(endDate);
      startDate.setMonth(endDate.getMonth() - 12);
  }

  return {
    start: startDate.toISOString().split('T')[0],
    end: endDateISO
  };
}

/**
 * Simplify polygon coordinates for smaller file size
 */
function simplifyCoordinates(coords, tolerance = 0.0001) {
  if (coords.length <= 4) return coords;

  const [start, end] = [coords[0], coords[coords.length - 1]];
  let maxDist = 0;
  let maxIndex = 0;

  for (let i = 1; i < coords.length - 1; i++) {
    const dist = perpendicularDistance(coords[i], start, end);
    if (dist > maxDist) {
      maxDist = dist;
      maxIndex = i;
    }
  }

  if (maxDist > tolerance) {
    const left = simplifyCoordinates(coords.slice(0, maxIndex + 1), tolerance);
    const right = simplifyCoordinates(coords.slice(maxIndex), tolerance);
    return left.slice(0, -1).concat(right);
  }

  return [start, end];
}

function perpendicularDistance(point, lineStart, lineEnd) {
  const [x, y] = point;
  const [x1, y1] = lineStart;
  const [x2, y2] = lineEnd;

  const dx = x2 - x1;
  const dy = y2 - y1;

  if (dx === 0 && dy === 0) {
    return Math.sqrt((x - x1) ** 2 + (y - y1) ** 2);
  }

  const t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy);
  const nearestX = x1 + t * dx;
  const nearestY = y1 + t * dy;

  return Math.sqrt((x - nearestX) ** 2 + (y - nearestY) ** 2);
}

function simplifyGeometry(geometry, tolerance = 0.0001) {
  if (geometry.type === 'Polygon') {
    return {
      type: 'Polygon',
      coordinates: geometry.coordinates.map(ring => simplifyCoordinates(ring, tolerance))
    };
  } else if (geometry.type === 'MultiPolygon') {
    return {
      type: 'MultiPolygon',
      coordinates: geometry.coordinates.map(polygon =>
        polygon.map(ring => simplifyCoordinates(ring, tolerance))
      )
    };
  }
  return geometry;
}

/**
 * Main compilation function
 */
async function compile() {
  console.log('='.repeat(60));
  console.log('SleepEasy Data Compiler');
  console.log('='.repeat(60));
  console.log(`Started: ${new Date().toISOString()}`);

  // Ensure output directory exists
  if (!fs.existsSync(CONFIG.OUTPUT_DIR)) {
    fs.mkdirSync(CONFIG.OUTPUT_DIR, { recursive: true });
  }

  try {
    // Step 1: Fetch NTA boundaries
    const boundaries = await fetchNTABoundaries();

    // Step 2: Estimate populations
    console.log('\n[2/4] Estimating populations...');
    const population = estimatePopulations(boundaries);
    console.log(`  Estimated population for ${Object.keys(population).length} NTAs`);

    const windowEndDateISO = fetchWindowEndDate();

    // Step 3 & 4: Fetch crimes once (max window), then compute all requested windows from that set.
    const allStats = {};
    const allComparisons = {};

    const windowRanges = CONFIG.TIME_WINDOWS.map((window) => ({
      window,
      ...getDateRange(window, windowEndDateISO)
    }));

    const earliestStart = windowRanges.map(r => r.start).filter(Boolean).sort()[0];
    if (!earliestStart) throw new Error('Could not determine earliest start date');

    console.log(`\n[3/4] Fetching crime data (from ${earliestStart} to ${windowEndDateISO})...`);
    const fetched = fetchCrimeData(earliestStart, windowEndDateISO);
    const crimes = fetched.crimes;
    const dataThroughISO = fetched.dataThrough || windowEndDateISO;

    const assigned = assignCrimesToNTAs(crimes, boundaries);
    const countsByWindow = computeCountsByWindow(assigned.events, boundaries, windowRanges);

    for (const r of windowRanges) {
      console.log(`\n${'='.repeat(40)}`);
      console.log(`Computing time window: ${r.window} (${r.start} to ${r.end})`);
      console.log('='.repeat(40));

      const ntaCrimes = countsByWindow[r.window];
      const { stats, comparisons } = computeStatistics(ntaCrimes, boundaries, population);

      allStats[r.window] = stats;
      allComparisons[r.window] = comparisons;
      stats._meta = { ...(stats._meta || {}), dataThrough: dataThroughISO };
    }

    // Prepare output data
    console.log('\n' + '='.repeat(40));
    console.log('Writing output files...');
    console.log('='.repeat(40));

    // Simplify boundaries for smaller file size
    const simplifiedBoundaries = {};
    for (const [ntaId, nta] of Object.entries(boundaries)) {
      simplifiedBoundaries[ntaId] = {
        id: nta.id,
        name: nta.name,
        borough: nta.borough,
        geometry: simplifyGeometry(nta.geometry, 0.0003) // ~30m tolerance
      };
    }

    // Write NTA boundaries
    const boundariesOutput = {
      generated: new Date().toISOString(),
      count: Object.keys(simplifiedBoundaries).length,
      boundaries: simplifiedBoundaries
    };

    const boundariesPath = path.join(CONFIG.OUTPUT_DIR, 'nta-boundaries.json');
    fs.writeFileSync(boundariesPath, JSON.stringify(boundariesOutput));
    console.log(`  Written: ${boundariesPath} (${(fs.statSync(boundariesPath).size / 1024).toFixed(1)} KB)`);

    // Use the most recent observed complaint date as our freshness marker.
    let dataThrough = null;
    for (const window of CONFIG.TIME_WINDOWS) {
      const d = allStats?.[window]?._meta?.dataThrough || null;
      if (!d) continue;
      if (!dataThrough || d > dataThrough) dataThrough = d;
      // Remove internal meta from output stats map.
      delete allStats[window]._meta;
    }

    // Write crime statistics
    const statsOutput = {
      generated: new Date().toISOString(),
      dataThrough: dataThrough || new Date().toISOString().split('T')[0],
      timeWindows: CONFIG.TIME_WINDOWS,
      stats: allStats,
      comparisons: allComparisons,
      methodologyVersion: '1.0.0'
    };

    const statsPath = path.join(CONFIG.OUTPUT_DIR, 'crime-stats.json');
    fs.writeFileSync(statsPath, JSON.stringify(statsOutput));
    console.log(`  Written: ${statsPath} (${(fs.statSync(statsPath).size / 1024).toFixed(1)} KB)`);

    // Print summary statistics
    console.log('\n' + '='.repeat(60));
    console.log('SUMMARY');
    console.log('='.repeat(60));
    console.log(`Total NTAs: ${Object.keys(simplifiedBoundaries).length}`);

    for (const window of CONFIG.TIME_WINDOWS) {
      const comp = allComparisons[window];
      console.log(`\n${window} NYC Averages:`);

      const labels = {
        murder: 'Murder',
        felonyAssault: 'Felony Assault',
        propertyCrime: 'Property Crime'
      };

      for (const key of Object.keys(CONFIG.CRIME_CATEGORIES)) {
        const label = labels[key] || key;
        console.log(`  ${label} rate: ${comp.nycAverage[key]} per 100k`);
      }
    }

    console.log('\n' + '='.repeat(60));
    console.log('Compilation completed successfully!');
    console.log(`Finished: ${new Date().toISOString()}`);
    console.log('='.repeat(60));

  } catch (error) {
    console.error('\nCompilation failed:', error.message);
    console.error(error.stack);
    process.exit(1);
  }
}

// Run if called directly
if (require.main === module) {
  compile();
}

module.exports = { compile };
