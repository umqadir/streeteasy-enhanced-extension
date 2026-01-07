/**
 * StreetSafe Utility Functions
 * Shared utilities for coordinate extraction, geocoding, and data fetching
 *
 * Note: This version uses static precompiled data for crime statistics
 * instead of a backend API. All data lookups happen client-side.
 */

// Configuration
const CONFIG = {
  NYC_GEOCODE_API: 'https://geosearch.planninglabs.nyc/v1/search',
  CACHE_TTL_MS: 1000 * 60 * 60 * 24, // 24 hours
  DEFAULT_TIME_WINDOW: '12m'
};

/**
 * Extract coordinates from Google Static Maps URL
 * @param {string} imageUrl - The static map image URL
 * @returns {Object|null} {lat, lon} or null
 */
function extractCoordinatesFromMapUrl(imageUrl) {
  try {
    const url = new URL(imageUrl);
    const centerParam = url.searchParams.get('center');

    if (centerParam) {
      const [lat, lon] = centerParam.split(',').map(parseFloat);
      if (!isNaN(lat) && !isNaN(lon)) {
        return { lat, lon };
      }
    }
  } catch (e) {
    console.error('[StreetSafe] Error parsing map URL:', e);
  }
  return null;
}

/**
 * Geocode an address using NYC GeoSearch API (Pelias)
 * @param {string} address - The address to geocode
 * @returns {Promise<Object|null>} {lat, lon, label} or null
 */
async function geocodeAddress(address) {
  if (!address || address.trim() === '') {
    return null;
  }

  try {
    const params = new URLSearchParams({
      text: address,
      size: 1,
      // Restrict to NYC bounds
      'boundary.rect.min_lat': 40.477399,
      'boundary.rect.min_lon': -74.259090,
      'boundary.rect.max_lat': 40.917577,
      'boundary.rect.max_lon': -73.700272
    });

    const response = await fetch(`${CONFIG.NYC_GEOCODE_API}?${params}`);

    if (!response.ok) {
      console.error('[StreetSafe] Geocoding API error:', response.status);
      return null;
    }

    const data = await response.json();

    if (data.features && data.features.length > 0) {
      const feature = data.features[0];
      const [lon, lat] = feature.geometry.coordinates;

      return {
        lat,
        lon,
        label: feature.properties.label || address
      };
    }
  } catch (e) {
    console.error('[StreetSafe] Geocoding error:', e);
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
      console.warn('[StreetSafe] Location not found in NYC boundaries');
      return null;
    }

    console.log('[StreetSafe] Found NTA:', nta.name, `(${nta.id})`);

    // Get crime statistics from precompiled data
    const stats = await crimeStatsManager.getStats(nta.id, window);

    if (!stats) {
      console.warn('[StreetSafe] No crime statistics for NTA:', nta.id);
      return null;
    }

    // Get comparisons with borough-specific data
    const comparisons = await crimeStatsManager.getComparisons(window, nta.borough);

    // Build response matching the original API format
    return {
      geography: {
        ntaId: nta.id,
        ntaName: nta.name,
        borough: nta.borough
      },
      metrics: stats.metrics,
      timeWindow: window,
      dataThrough: stats.dataThrough,
      computedAt: new Date().toISOString(),
      comparisons: comparisons || { nycAverage: {}, boroughAverage: {} },
      methodologyVersion: stats.methodologyVersion
    };

  } catch (e) {
    console.error('[StreetSafe] Error fetching crime stats:', e);
    return null;
  }
}

/**
 * Format a number with commas
 * @param {number} num - Number to format
 * @returns {string} Formatted number
 */
function formatNumber(num) {
  return num.toLocaleString('en-US');
}

/**
 * Format a rate per 100k
 * @param {number} rate - Rate to format
 * @returns {string} Formatted rate
 */
function formatRate(rate) {
  return rate.toFixed(1);
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

/**
 * Cache helper for storing data in chrome.storage.local
 */
const Cache = {
  async get(key) {
    try {
      const result = await chrome.storage.local.get(key);
      if (result[key]) {
        const { data, timestamp } = result[key];
        if (Date.now() - timestamp < CONFIG.CACHE_TTL_MS) {
          return data;
        }
      }
    } catch (e) {
      console.error('[StreetSafe] Cache get error:', e);
    }
    return null;
  },

  async set(key, data) {
    try {
      await chrome.storage.local.set({
        [key]: {
          data,
          timestamp: Date.now()
        }
      });
    } catch (e) {
      console.error('[StreetSafe] Cache set error:', e);
    }
  }
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    CONFIG,
    extractCoordinatesFromMapUrl,
    geocodeAddress,
    fetchCrimeStats,
    formatNumber,
    formatRate,
    formatPercentile,
    getPercentileColorClass,
    Cache
  };
}
