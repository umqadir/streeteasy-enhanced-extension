/**
 * StreetSafe Utility Functions
 * Shared utilities for coordinate extraction, geocoding, and data fetching
 */

// Configuration
const CONFIG = {
  API_BASE_URL: 'http://localhost:3000/v1', // Backend API
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
 * Fetch crime statistics from backend API
 * @param {number} lat - Latitude
 * @param {number} lon - Longitude
 * @param {string} window - Time window (e.g., '12m', '24m')
 * @returns {Promise<Object|null>} Crime statistics data
 */
async function fetchCrimeStats(lat, lon, window = CONFIG.DEFAULT_TIME_WINDOW) {
  try {
    const params = new URLSearchParams({
      lat: lat.toString(),
      lon: lon.toString(),
      window
    });

    const response = await fetch(`${CONFIG.API_BASE_URL}/safety?${params}`);

    if (!response.ok) {
      console.error('[StreetSafe] API error:', response.status);
      return null;
    }

    const data = await response.json();
    return data;
  } catch (e) {
    console.error('[StreetSafe] Fetch error:', e);
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
