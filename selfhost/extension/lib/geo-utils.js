/**
 * SleepEasy Geo Utilities
 * Client-side geographic utilities for NTA lookup
 */

// Minimal logger (disabled by default). To debug, set `globalThis.__SLEEPEASY_DEBUG__ = true`
// in the "Content scripts" execution context and reload the page.
if (!globalThis.SleepEasyLog) {
  globalThis.__SLEEPEASY_DEBUG__ = globalThis.__SLEEPEASY_DEBUG__ === true;
  globalThis.SleepEasyLog = {
    debug: (...args) => { if (globalThis.__SLEEPEASY_DEBUG__) console.log(...args); },
    warn: (...args) => { if (globalThis.__SLEEPEASY_DEBUG__) console.warn(...args); },
    error: (...args) => { if (globalThis.__SLEEPEASY_DEBUG__) console.error(...args); }
  };
}

// Back-compat for older builds.
globalThis.StreetSafeLog = globalThis.SleepEasyLog;

const EARTH_RADIUS_M = 6378137; // WGS84 spheroid major axis (good enough for local area approx)
const SQ_METERS_PER_SQ_MILE = 2589988.110336;

function toRad(deg) {
  return (deg * Math.PI) / 180;
}

function projectLonLatToMeters(lon, lat, refLatRad) {
  const x = toRad(lon) * EARTH_RADIUS_M * Math.cos(refLatRad);
  const y = toRad(lat) * EARTH_RADIUS_M;
  return [x, y];
}

function ringAreaSqMeters(ring) {
  if (!Array.isArray(ring) || ring.length < 3) return 0;

  // Use the ring's mean latitude as an equirectangular reference latitude.
  let sumLat = 0;
  let count = 0;
  for (const coord of ring) {
    if (!coord || coord.length < 2) continue;
    sumLat += coord[1];
    count += 1;
  }
  const refLatRad = toRad(count ? sumLat / count : 40.72);

  const n = ring.length;
  let twiceArea = 0;
  for (let i = 0; i < n; i++) {
    const [lon1, lat1] = ring[i];
    const [lon2, lat2] = ring[(i + 1) % n];
    const [x1, y1] = projectLonLatToMeters(lon1, lat1, refLatRad);
    const [x2, y2] = projectLonLatToMeters(lon2, lat2, refLatRad);
    twiceArea += (x1 * y2) - (x2 * y1);
  }

  return Math.abs(twiceArea) / 2;
}

function geometryAreaSqMeters(geometry) {
  if (!geometry) return 0;

  if (geometry.type === 'Polygon') {
    const rings = geometry.coordinates || [];
    if (rings.length === 0) return 0;
    const outer = ringAreaSqMeters(rings[0]);
    const holes = rings.slice(1).reduce((acc, r) => acc + ringAreaSqMeters(r), 0);
    return Math.max(0, outer - holes);
  }

  if (geometry.type === 'MultiPolygon') {
    const polys = geometry.coordinates || [];
    let total = 0;
    for (const poly of polys) {
      if (!poly || poly.length === 0) continue;
      const outer = ringAreaSqMeters(poly[0]);
      const holes = poly.slice(1).reduce((acc, r) => acc + ringAreaSqMeters(r), 0);
      total += Math.max(0, outer - holes);
    }
    return total;
  }

  return 0;
}

function roundTo(num, decimals) {
  const factor = 10 ** decimals;
  return Math.round(num * factor) / factor;
}

/**
 * Simple point-in-polygon test using ray casting algorithm
 * @param {number[]} point - [lon, lat] coordinates
 * @param {number[][]} polygon - Array of [lon, lat] coordinate pairs forming the polygon ring
 * @returns {boolean} True if point is inside polygon
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
 * Check if a point is inside a GeoJSON geometry (Polygon or MultiPolygon)
 * @param {number} lon - Longitude
 * @param {number} lat - Latitude
 * @param {Object} geometry - GeoJSON geometry object
 * @returns {boolean} True if point is inside geometry
 */
function pointInGeometry(lon, lat, geometry) {
  const point = [lon, lat];

  if (geometry.type === 'Polygon') {
    // Check if point is inside outer ring
    if (!pointInPolygon(point, geometry.coordinates[0])) {
      return false;
    }
    // Check if point is inside any holes
    for (let i = 1; i < geometry.coordinates.length; i++) {
      if (pointInPolygon(point, geometry.coordinates[i])) {
        return false; // Point is in a hole
      }
    }
    return true;
  } else if (geometry.type === 'MultiPolygon') {
    // Check each polygon in the MultiPolygon
    for (const polygon of geometry.coordinates) {
      // Check outer ring
      if (pointInPolygon(point, polygon[0])) {
        // Check if point is inside any holes
        let inHole = false;
        for (let i = 1; i < polygon.length; i++) {
          if (pointInPolygon(point, polygon[i])) {
            inHole = true;
            break;
          }
        }
        if (!inHole) {
          return true;
        }
      }
    }
  }

  return false;
}

/**
 * NTA (Neighborhood Tabulation Area) Lookup Manager
 * Handles loading NTA boundaries and performing point-in-polygon lookups
 */
class NTALookup {
  constructor() {
    this.boundaries = null;
    this.loaded = false;
    this.loading = false;
  }

  /**
   * Load NTA boundaries data
   * @returns {Promise<boolean>} True if loaded successfully
   */
  async load() {
    if (this.loaded) return true;
    if (this.loading) {
      // Wait for ongoing load to complete
      while (this.loading) {
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      return this.loaded;
    }

    this.loading = true;

    try {
      // Load from extension's data directory
      const url = chrome.runtime.getURL('data/nta-boundaries.json');
      const response = await fetch(url);

      if (!response.ok) {
        throw new Error(`Failed to load NTA boundaries: ${response.status}`);
      }

      const data = await response.json();
      this.boundaries = data.boundaries;

      // Precompute approximate land area (sq mi) for density-style measures.
      for (const nta of Object.values(this.boundaries)) {
        try {
          const areaM2 = geometryAreaSqMeters(nta.geometry);
          nta.areaSqMi = roundTo(areaM2 / SQ_METERS_PER_SQ_MILE, 4);
        } catch {
          nta.areaSqMi = null;
        }
      }

      this.loaded = true;
      SleepEasyLog.debug(`[SleepEasy] Loaded ${Object.keys(this.boundaries).length} NTA boundaries`);
      return true;
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error loading NTA boundaries:', error);
      return false;
    } finally {
      this.loading = false;
    }
  }

  /**
   * Find the NTA containing a given point
   * @param {number} lat - Latitude
   * @param {number} lon - Longitude
   * @returns {Promise<Object|null>} NTA info {id, name, borough} or null if not found
   */
  async findNTA(lat, lon) {
    if (!this.loaded) {
      const success = await this.load();
      if (!success) return null;
    }

    // Search through all NTAs
    for (const [ntaId, nta] of Object.entries(this.boundaries)) {
      if (pointInGeometry(lon, lat, nta.geometry)) {
        return {
          id: nta.id,
          name: nta.name,
          borough: nta.borough
        };
      }
    }

    return null;
  }

  /**
   * Get NTA info by ID
   * @param {string} ntaId - NTA identifier
   * @returns {Promise<Object|null>} NTA info or null
   */
  async getNTA(ntaId) {
    if (!this.loaded) {
      const success = await this.load();
      if (!success) return null;
    }

    const nta = this.boundaries[ntaId];
    if (nta) {
      return {
        id: nta.id,
        name: nta.name,
        borough: nta.borough
      };
    }
    return null;
  }
}

/**
 * Crime Statistics Manager
 * Handles loading and querying precomputed crime statistics
 */
class CrimeStatsManager {
  constructor() {
    this.data = null;
    this.loaded = false;
    this.loading = false;
  }

  /**
   * Load crime statistics data
   * @returns {Promise<boolean>} True if loaded successfully
   */
  async load() {
    if (this.loaded) return true;
    if (this.loading) {
      while (this.loading) {
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      return this.loaded;
    }

    this.loading = true;

    try {
      const url = chrome.runtime.getURL('data/crime-stats.json');
      const response = await fetch(url);

      if (!response.ok) {
        throw new Error(`Failed to load crime stats: ${response.status}`);
      }

      this.data = await response.json();
      this.loaded = true;
      SleepEasyLog.debug('[SleepEasy] Crime statistics loaded');
      return true;
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error loading crime stats:', error);
      return false;
    } finally {
      this.loading = false;
    }
  }

  /**
   * Get crime statistics for an NTA
   * @param {string} ntaId - NTA identifier
   * @param {string} timeWindow - Time window ('12m', '24m', 'ytd')
   * @returns {Promise<Object|null>} Crime stats or null
   */
  async getStats(ntaId, timeWindow = '24m') {
    if (!this.loaded) {
      const success = await this.load();
      if (!success) return null;
    }

    const windowStats = this.data.stats[timeWindow];
    if (!windowStats) return null;

    const ntaStats = windowStats[ntaId];
    if (!ntaStats) return null;

    return {
      metrics: ntaStats,
      timeWindow,
      dataThrough: this.data.dataThrough,
      comparisons: this.data.comparisons[timeWindow],
      methodologyVersion: this.data.methodologyVersion
    };
  }

  /**
   * Get comparisons (NYC and borough averages)
   * @param {string} timeWindow - Time window
   * @param {string} borough - Borough name (optional, for borough-specific averages)
   * @returns {Promise<Object|null>}
   */
  async getComparisons(timeWindow = '24m', borough = null) {
    if (!this.loaded) {
      const success = await this.load();
      if (!success) return null;
    }

    const comparisons = this.data.comparisons[timeWindow];
    if (!comparisons) return null;

    const result = {
      nycAverage: comparisons.nycAverage
    };

    if (borough && comparisons.boroughAverage && comparisons.boroughAverage[borough]) {
      result.boroughAverage = comparisons.boroughAverage[borough];
    }

    return result;
  }

  /**
   * Get data freshness date
   * @returns {Promise<string|null>}
   */
  async getDataDate() {
    if (!this.loaded) {
      const success = await this.load();
      if (!success) return null;
    }
    return this.data.dataThrough;
  }
}

/**
 * NTA Exposure Manager
 * Loads resident population (2020) + LODES jobs (WAC) for ambient denominators.
 */
class NTAExposureManager {
  constructor() {
    this.exposures = null;
    this.loaded = false;
    this.loading = false;
    this.meta = null;
  }

  async load() {
    if (this.loaded) return true;
    if (this.loading) {
      while (this.loading) {
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      return this.loaded;
    }

    this.loading = true;

    try {
      const url = chrome.runtime.getURL('data/nta-exposure.json');
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Failed to load NTA exposure: ${response.status}`);
      }

      const data = await response.json();
      this.exposures = data.exposures || null;
      this.meta = {
        generatedAt: data.generatedAt || null,
        populationYear: data.populationYear || null,
        lodesYear: data.lodesYear || null
      };

      this.loaded = true;
      SleepEasyLog.debug(`[SleepEasy] NTA exposure loaded (${this.exposures ? Object.keys(this.exposures).length : 0} NTAs)`);
      return true;
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error loading NTA exposure:', error);
      return false;
    } finally {
      this.loading = false;
    }
  }

  async getExposure(ntaId) {
    if (!this.loaded) {
      const success = await this.load();
      if (!success) return null;
    }
    return this.exposures?.[ntaId] || null;
  }
}

// Create singleton instances
const ntaLookup = new NTALookup();
const crimeStatsManager = new CrimeStatsManager();
const ntaExposureManager = new NTAExposureManager();

// Export for use in other scripts
if (typeof window !== 'undefined') {
  window.NTALookup = NTALookup;
  window.CrimeStatsManager = CrimeStatsManager;
  window.NTAExposureManager = NTAExposureManager;
  window.ntaLookup = ntaLookup;
  window.crimeStatsManager = crimeStatsManager;
  window.ntaExposureManager = ntaExposureManager;
  window.pointInPolygon = pointInPolygon;
  window.pointInGeometry = pointInGeometry;
}
