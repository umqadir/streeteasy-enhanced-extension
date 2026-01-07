#!/usr/bin/env node

/**
 * StreetSafe Data Compiler
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
  CRIME_DATA_URL: 'https://data.cityofnewyork.us/resource/5uac-w243.json',

  // Output paths
  OUTPUT_DIR: path.join(__dirname, '..', 'extension', 'data'),

  // Crime categories to track
  CRIME_CATEGORIES: {
    murder: 'MURDER & NON-NEGL. MANSLAUGHTER',
    felonyAssault: 'FELONY ASSAULT'
  },

  // Time windows to compute
  TIME_WINDOWS: ['12m', '24m', 'ytd'],

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
    const result = execSync(`curl -s --max-time 120 '${escapedUrl}'`, {
      encoding: 'utf8',
      maxBuffer: 100 * 1024 * 1024 // 100MB buffer
    });

    const parsed = JSON.parse(result);
    if (!Array.isArray(parsed)) {
      console.log('  Response:', typeof parsed, JSON.stringify(parsed).substring(0, 200));
    }
    return parsed;
  } catch (e) {
    console.error('  Raw output:', e.stdout?.substring(0, 500));
    throw new Error(`Failed to fetch ${url}: ${e.message}`);
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
function fetchCrimeData(startDate, endDate) {
  console.log(`  Fetching crimes from ${startDate} to ${endDate}...`);

  const offenseFilter = Object.values(CONFIG.CRIME_CATEGORIES)
    .map(cat => `ofns_desc='${cat}'`)
    .join(' OR ');

  // SoQL query for crime data
  const where = encodeURIComponent(
    `cmplnt_fr_dt >= '${startDate}' AND cmplnt_fr_dt <= '${endDate}' AND (${offenseFilter}) AND latitude IS NOT NULL AND longitude IS NOT NULL`
  );

  const url = `${CONFIG.CRIME_DATA_URL}?$where=${where}&$limit=100000&$select=cmplnt_num,cmplnt_fr_dt,ofns_desc,latitude,longitude`;

  const data = fetchJSON(url);
  console.log(`  Fetched ${data.length} crime complaints`);

  return data;
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

/**
 * Assign crime complaints to NTAs based on coordinates
 */
function assignCrimesToNTAs(crimes, boundaries) {
  console.log('\n  Assigning crimes to NTAs...');

  const ntaCrimes = {};
  let assigned = 0;
  let unassigned = 0;

  // Initialize counts for all NTAs
  for (const ntaId of Object.keys(boundaries)) {
    ntaCrimes[ntaId] = {
      murder: [],
      felonyAssault: []
    };
  }

  // Assign each crime to an NTA
  for (const crime of crimes) {
    const lon = parseFloat(crime.longitude);
    const lat = parseFloat(crime.latitude);

    if (isNaN(lon) || isNaN(lat)) {
      unassigned++;
      continue;
    }

    let foundNTA = null;
    for (const [ntaId, nta] of Object.entries(boundaries)) {
      if (pointInGeometry(lon, lat, nta.geometry)) {
        foundNTA = ntaId;
        break;
      }
    }

    if (foundNTA) {
      const offense = crime.ofns_desc?.toUpperCase() || '';
      if (offense.includes('MURDER')) {
        ntaCrimes[foundNTA].murder.push(crime);
      } else if (offense.includes('ASSAULT')) {
        ntaCrimes[foundNTA].felonyAssault.push(crime);
      }
      assigned++;
    } else {
      unassigned++;
    }
  }

  console.log(`  Assigned ${assigned} crimes, ${unassigned} could not be assigned`);
  return ntaCrimes;
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
      const crimes = ntaCrimes[ntaId]?.[metricKey] || [];
      const pop = population[ntaId] || CONFIG.DEFAULT_POPULATION;
      const count = crimes.length;
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
  const boroughStats = {};
  const nycStats = { totalPop: 0, murder: 0, felonyAssault: 0 };

  for (const [ntaId, nta] of Object.entries(boundaries)) {
    const pop = population[ntaId] || CONFIG.DEFAULT_POPULATION;
    const crimes = ntaCrimes[ntaId] || { murder: [], felonyAssault: [] };

    const borough = nta.borough;
    if (!boroughStats[borough]) {
      boroughStats[borough] = { totalPop: 0, murder: 0, felonyAssault: 0 };
    }

    boroughStats[borough].totalPop += pop;
    boroughStats[borough].murder += crimes.murder.length;
    boroughStats[borough].felonyAssault += crimes.felonyAssault.length;

    nycStats.totalPop += pop;
    nycStats.murder += crimes.murder.length;
    nycStats.felonyAssault += crimes.felonyAssault.length;
  }

  const comparisons = {
    nycAverage: {
      murder: nycStats.totalPop > 0 ? Math.round((nycStats.murder / nycStats.totalPop) * 100000 * 100) / 100 : 0,
      felonyAssault: nycStats.totalPop > 0 ? Math.round((nycStats.felonyAssault / nycStats.totalPop) * 100000 * 100) / 100 : 0
    },
    boroughAverage: {}
  };

  for (const [borough, stats] of Object.entries(boroughStats)) {
    comparisons.boroughAverage[borough] = {
      murder: stats.totalPop > 0 ? Math.round((stats.murder / stats.totalPop) * 100000 * 100) / 100 : 0,
      felonyAssault: stats.totalPop > 0 ? Math.round((stats.felonyAssault / stats.totalPop) * 100000 * 100) / 100 : 0
    };
  }

  return comparisons;
}

/**
 * Get date range for a time window
 */
function getDateRange(window) {
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
  console.log('StreetSafe Data Compiler');
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

    // Step 3 & 4: Fetch crime data and compute statistics for each time window
    const allStats = {};
    const allComparisons = {};

    for (const window of CONFIG.TIME_WINDOWS) {
      console.log(`\n${'='.repeat(40)}`);
      console.log(`Processing time window: ${window}`);
      console.log('='.repeat(40));

      const dateRange = getDateRange(window);
      console.log(`\n[3/4] Fetching crime data for ${window}...`);
      const crimes = fetchCrimeData(dateRange.start, dateRange.end);
      const ntaCrimes = assignCrimesToNTAs(crimes, boundaries);
      const { stats, comparisons } = computeStatistics(ntaCrimes, boundaries, population);

      allStats[window] = stats;
      allComparisons[window] = comparisons;
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

    // Write crime statistics
    const statsOutput = {
      generated: new Date().toISOString(),
      dataThrough: new Date().toISOString().split('T')[0],
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
      console.log(`  Murder rate: ${comp.nycAverage.murder} per 100k`);
      console.log(`  Felony Assault rate: ${comp.nycAverage.felonyAssault} per 100k`);
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
