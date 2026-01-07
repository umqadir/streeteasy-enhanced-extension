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

  // Population estimates by NTA (2020 Census / ACS estimates)
  // This is a fallback - we'll try to fetch real data
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
 * Get estimated population for NTAs
 * Uses shape_area as proxy if no population data available
 */
function estimatePopulations(boundaries) {
  const populations = {};

  // Population density assumptions by borough (people per sq meter)
  const densityByBorough = {
    'Manhattan': 0.027,    // ~27,000/km²
    'Brooklyn': 0.014,     // ~14,000/km²
    'Bronx': 0.013,        // ~13,000/km²
    'Queens': 0.008,       // ~8,000/km²
    'Staten Island': 0.003 // ~3,000/km²
  };

  for (const [ntaId, nta] of Object.entries(boundaries)) {
    // Default to a reasonable NYC neighborhood population
    populations[ntaId] = CONFIG.DEFAULT_POPULATION;
  }

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
