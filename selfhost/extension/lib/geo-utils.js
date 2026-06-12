/**
 * SleepEasy geo utilities.
 *
 * Client-side NTA (Neighborhood Tabulation Area) lookup plus loaders for the
 * three data files that ship with the extension:
 *   data/nta-boundaries.json  - simplified NTA polygons (197 residential NTAs)
 *   data/crime-stats.json     - precompiled NYPD complaint stats per NTA/window
 *   data/nta-exposure.json    - 2020 census population + LODES jobs per NTA
 *
 * No network requests: everything is bundled and read via chrome.runtime.getURL.
 */

// Minimal logger (silent by default). To debug, set
// `globalThis.__SLEEPEASY_DEBUG__ = true` in the content-script context and reload.
if (!globalThis.SleepEasyLog) {
  globalThis.__SLEEPEASY_DEBUG__ = globalThis.__SLEEPEASY_DEBUG__ === true;
  globalThis.SleepEasyLog = {
    debug: (...args) => { if (globalThis.__SLEEPEASY_DEBUG__) console.log(...args); },
    warn: (...args) => { if (globalThis.__SLEEPEASY_DEBUG__) console.warn(...args); },
    error: (...args) => { if (globalThis.__SLEEPEASY_DEBUG__) console.error(...args); }
  };
}

const EARTH_RADIUS_M = 6378137; // WGS84 major axis; fine for local-area approximations
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

function geometryBBox(geometry) {
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  const scanRing = (ring) => {
    for (const [lon, lat] of ring) {
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
  };
  if (geometry.type === 'Polygon') {
    for (const ring of geometry.coordinates) scanRing(ring);
  } else if (geometry.type === 'MultiPolygon') {
    for (const poly of geometry.coordinates) for (const ring of poly) scanRing(ring);
  }
  return { minLon, minLat, maxLon, maxLat };
}

function roundTo(num, decimals) {
  const factor = 10 ** decimals;
  return Math.round(num * factor) / factor;
}

/**
 * Ray-casting point-in-polygon test.
 * @param {number[]} point - [lon, lat]
 * @param {number[][]} polygon - ring of [lon, lat] pairs
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
 * Point-in-geometry test for GeoJSON Polygon / MultiPolygon (holes respected).
 */
function pointInGeometry(lon, lat, geometry) {
  const point = [lon, lat];

  if (geometry.type === 'Polygon') {
    if (!pointInPolygon(point, geometry.coordinates[0])) return false;
    for (let i = 1; i < geometry.coordinates.length; i++) {
      if (pointInPolygon(point, geometry.coordinates[i])) return false; // in a hole
    }
    return true;
  }

  if (geometry.type === 'MultiPolygon') {
    for (const polygon of geometry.coordinates) {
      if (pointInPolygon(point, polygon[0])) {
        let inHole = false;
        for (let i = 1; i < polygon.length; i++) {
          if (pointInPolygon(point, polygon[i])) { inHole = true; break; }
        }
        if (!inHole) return true;
      }
    }
  }

  return false;
}

/**
 * Shared single-flight loader: load() resolves once, concurrent callers await
 * the same promise.
 */
class JsonResourceLoader {
  constructor(path) {
    this._path = path;
    this._promise = null;
  }
  load() {
    if (!this._promise) {
      this._promise = (async () => {
        const url = chrome.runtime.getURL(this._path);
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`Failed to load ${this._path}: ${response.status}`);
        }
        return response.json();
      })().catch(err => {
        this._promise = null; // allow retry after a failed load
        throw err;
      });
    }
    return this._promise;
  }
}

/**
 * NTA boundary lookup with precomputed bboxes and areas.
 */
class NTALookup {
  constructor() {
    this.boundaries = null;
    this._loader = new JsonResourceLoader('data/nta-boundaries.json');
  }

  async load() {
    if (this.boundaries) return true;
    try {
      const data = await this._loader.load();
      const boundaries = data.boundaries;

      for (const nta of Object.values(boundaries)) {
        nta.bbox = geometryBBox(nta.geometry);
        try {
          nta.areaSqMi = roundTo(geometryAreaSqMeters(nta.geometry) / SQ_METERS_PER_SQ_MILE, 4);
        } catch {
          nta.areaSqMi = null;
        }
      }

      this.boundaries = boundaries;
      SleepEasyLog.debug(`[SleepEasy] Loaded ${Object.keys(boundaries).length} NTA boundaries`);
      return true;
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error loading NTA boundaries:', error);
      return false;
    }
  }

  /**
   * Find the NTA containing a point.
   * @returns {Promise<{id, name, borough}|null>}
   */
  async findNTA(lat, lon) {
    if (!(await this.load())) return null;

    for (const nta of Object.values(this.boundaries)) {
      const b = nta.bbox;
      if (b && (lon < b.minLon || lon > b.maxLon || lat < b.minLat || lat > b.maxLat)) continue;
      if (pointInGeometry(lon, lat, nta.geometry)) {
        return { id: nta.id, name: nta.name, borough: nta.borough };
      }
    }

    return null;
  }

  async getNTA(ntaId) {
    if (!(await this.load())) return null;
    const nta = this.boundaries[ntaId];
    return nta ? { id: nta.id, name: nta.name, borough: nta.borough } : null;
  }
}

/**
 * Precompiled crime statistics (per NTA, per time window).
 */
class CrimeStatsManager {
  constructor() {
    this.data = null;
    this._loader = new JsonResourceLoader('data/crime-stats.json');
  }

  async load() {
    if (this.data) return true;
    try {
      this.data = await this._loader.load();
      SleepEasyLog.debug('[SleepEasy] Crime statistics loaded');
      return true;
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error loading crime stats:', error);
      return false;
    }
  }

  async getStats(ntaId, timeWindow = '24m') {
    if (!(await this.load())) return null;

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

  async getComparisons(timeWindow = '24m', borough = null) {
    if (!(await this.load())) return null;

    const comparisons = this.data.comparisons[timeWindow];
    if (!comparisons) return null;

    const result = { nycAverage: comparisons.nycAverage };
    if (borough && comparisons.boroughAverage && comparisons.boroughAverage[borough]) {
      result.boroughAverage = comparisons.boroughAverage[borough];
    }
    return result;
  }

  async getDataDate() {
    if (!(await this.load())) return null;
    return this.data.dataThrough;
  }
}

/**
 * Resident population (2020 census) + LODES jobs per NTA, used as the
 * denominator for the ambient risk measure.
 */
class NTAExposureManager {
  constructor() {
    this.exposures = null;
    this.meta = null;
    this._loader = new JsonResourceLoader('data/nta-exposure.json');
  }

  async load() {
    if (this.exposures) return true;
    try {
      const data = await this._loader.load();
      this.exposures = data.exposures || null;
      this.meta = {
        generatedAt: data.generatedAt || null,
        populationYear: data.populationYear || null,
        lodesYear: data.lodesYear || null
      };
      SleepEasyLog.debug(`[SleepEasy] NTA exposure loaded (${this.exposures ? Object.keys(this.exposures).length : 0} NTAs)`);
      return true;
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error loading NTA exposure:', error);
      return false;
    }
  }

  async getExposure(ntaId) {
    if (!(await this.load())) return null;
    return this.exposures?.[ntaId] || null;
  }
}

// Singletons shared by the content scripts
const ntaLookup = new NTALookup();
const crimeStatsManager = new CrimeStatsManager();
const ntaExposureManager = new NTAExposureManager();

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
