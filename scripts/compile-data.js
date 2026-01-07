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

const https = require('https');
const fs = require('fs');
const path = require('path');

// Configuration
const CONFIG = {
  // NYC Open Data endpoints
  NTA_BOUNDARIES_URL: 'https://data.cityofnewyork.us/resource/9nt8-h7nd.json',
  CRIME_DATA_URL: 'https://data.cityofnewyork.us/resource/5uac-w243.json',
  POPULATION_URL: 'https://data.cityofnewyork.us/resource/swpk-hqdp.json',

  // Output paths
  OUTPUT_DIR: path.join(__dirname, '..', 'extension', 'data'),

  // Crime categories to track
  CRIME_CATEGORIES: {
    murder: 'MURDER & NON-NEGL. MANSLAUGHTER',
    felonyAssault: 'FELONY ASSAULT'
  },

  // Time windows to compute
  TIME_WINDOWS: ['12m', '24m', 'ytd'],

  // API request settings
  REQUEST_LIMIT: 50000,
  REQUEST_TIMEOUT: 60000
};

/**
 * Make an HTTPS GET request and return JSON
 */
function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    console.log(`Fetching: ${url.substring(0, 100)}...`);

    const request = https.get(url, { timeout: CONFIG.REQUEST_TIMEOUT }, (res) => {
      let data = '';

      res.on('data', (chunk) => {
        data += chunk;
      });

      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse JSON: ${e.message}`));
        }
      });
    });

    request.on('error', reject);
    request.on('timeout', () => {
      request.destroy();
      reject(new Error('Request timeout'));
    });
  });
}

/**
 * Fetch NTA boundaries from NYC Open Data
 */
async function fetchNTABoundaries() {
  console.log('\n[1/4] Fetching NTA boundaries...');

  // Fetch NTA data with geometry
  const url = `${CONFIG.NTA_BOUNDARIES_URL}?$limit=500&$select=ntacode,ntaname,boroname,the_geom`;
  const data = await fetchJSON(url);

  console.log(`  Fetched ${data.length} NTA records`);

  // Transform to simplified format
  const boundaries = {};

  for (const nta of data) {
    if (!nta.ntacode || !nta.the_geom) continue;

    // Skip non-residential NTAs (parks, airports, cemeteries)
    if (nta.ntacode.startsWith('park') ||
        nta.ntacode.includes('99') ||
        nta.ntaname?.toLowerCase().includes('cemetery') ||
        nta.ntaname?.toLowerCase().includes('airport')) {
      continue;
    }

    try {
      const geometry = typeof nta.the_geom === 'string'
        ? JSON.parse(nta.the_geom)
        : nta.the_geom;

      boundaries[nta.ntacode] = {
        id: nta.ntacode,
        name: nta.ntaname || nta.ntacode,
        borough: nta.boroname || 'Unknown',
        geometry: geometry
      };
    } catch (e) {
      console.warn(`  Warning: Could not parse geometry for ${nta.ntacode}`);
    }
  }

  console.log(`  Processed ${Object.keys(boundaries).length} valid NTAs`);
  return boundaries;
}

/**
 * Fetch population data for NTAs
 */
async function fetchPopulationData() {
  console.log('\n[2/4] Fetching population data...');

  // Try to get population data from census/ACS data
  // Using a general NYC population dataset
  const url = `${CONFIG.POPULATION_URL}?$limit=500`;

  try {
    const data = await fetchJSON(url);

    const population = {};
    for (const row of data) {
      const ntaCode = row.nta_code || row.ntacode || row.nta;
      const pop = parseInt(row.population || row.pop_2020 || row.total_population || 0);

      if (ntaCode && pop > 0) {
        population[ntaCode] = pop;
      }
    }

    console.log(`  Fetched population for ${Object.keys(population).length} NTAs`);
    return population;
  } catch (e) {
    console.warn(`  Warning: Could not fetch population data: ${e.message}`);
    console.log('  Using default population estimates...');
    return {};
  }
}

/**
 * Fetch crime complaint data
 */
async function fetchCrimeData(startDate, endDate) {
  console.log(`\n[3/4] Fetching crime data (${startDate} to ${endDate})...`);

  const offenseFilter = Object.values(CONFIG.CRIME_CATEGORIES)
    .map(cat => `ofns_desc='${encodeURIComponent(cat)}'`)
    .join(' OR ');

  const where = encodeURIComponent(
    `cmplnt_fr_dt >= '${startDate}' AND cmplnt_fr_dt <= '${endDate}' AND (${offenseFilter}) AND latitude IS NOT NULL AND longitude IS NOT NULL`
  );

  const url = `${CONFIG.CRIME_DATA_URL}?$where=${where}&$limit=${CONFIG.REQUEST_LIMIT}&$select=cmplnt_num,cmplnt_fr_dt,ofns_desc,latitude,longitude`;

  const data = await fetchJSON(url);
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
    // Check outer ring
    if (!pointInPolygon(point, geometry.coordinates[0])) return false;
    // Check holes
    for (let i = 1; i < geometry.coordinates.length; i++) {
      if (pointInPolygon(point, geometry.coordinates[i])) return false;
    }
    return true;
  } else if (geometry.type === 'MultiPolygon') {
    for (const polygon of geometry.coordinates) {
      if (pointInPolygon(point, polygon[0])) {
        // Check holes
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
      // Determine crime category
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
 * Compute statistics for all NTAs
 */
function computeStatistics(ntaCrimes, boundaries, population) {
  console.log('\n[4/4] Computing statistics...');

  // Default population for NTAs without data (NYC average NTA population ~44k)
  const DEFAULT_POPULATION = 44000;

  const stats = {};
  const ntaIds = Object.keys(boundaries);

  for (const metricKey of Object.keys(CONFIG.CRIME_CATEGORIES)) {
    // Collect all rates for ranking
    const rateData = [];

    for (const ntaId of ntaIds) {
      const crimes = ntaCrimes[ntaId]?.[metricKey] || [];
      const pop = population[ntaId] || DEFAULT_POPULATION;
      const count = crimes.length;
      const rate = pop > 0 ? (count / pop) * 100000 : 0;

      rateData.push({ ntaId, count, rate, population: pop });
    }

    // Sort by rate ascending for ranking (lower rate = better = lower rank number)
    rateData.sort((a, b) => a.rate - b.rate);

    // Assign ranks and percentiles
    const total = rateData.length;
    rateData.forEach((item, index) => {
      const rank = index + 1;
      // Percentile: what percentage of neighborhoods have HIGHER crime rates
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
  const DEFAULT_POPULATION = 44000;

  // Compute by borough
  const boroughStats = {};
  const nycStats = { totalPop: 0, murder: 0, felonyAssault: 0 };

  for (const [ntaId, nta] of Object.entries(boundaries)) {
    const pop = population[ntaId] || DEFAULT_POPULATION;
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

  // Calculate rates
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
 * Uses Douglas-Peucker-like simplification
 */
function simplifyCoordinates(coords, tolerance = 0.0001) {
  if (coords.length <= 2) return coords;

  // Find point with max distance from line
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

  // If max distance exceeds tolerance, recursively simplify
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

    // Step 2: Fetch population data
    const population = await fetchPopulationData();

    // Step 3 & 4: Fetch crime data and compute statistics for each time window
    const allStats = {};
    const allComparisons = {};

    for (const window of CONFIG.TIME_WINDOWS) {
      console.log(`\n${'='.repeat(40)}`);
      console.log(`Processing time window: ${window}`);
      console.log('='.repeat(40));

      const dateRange = getDateRange(window);
      const crimes = await fetchCrimeData(dateRange.start, dateRange.end);
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
        geometry: simplifyGeometry(nta.geometry, 0.0002) // ~20m tolerance
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

    console.log('\n' + '='.repeat(60));
    console.log('Compilation completed successfully!');
    console.log(`Finished: ${new Date().toISOString()}`);
    console.log('='.repeat(60));

  } catch (error) {
    console.error('\nCompilation failed:', error.message);
    process.exit(1);
  }
}

// Run if called directly
if (require.main === module) {
  compile();
}

module.exports = { compile };
