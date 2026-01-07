/**
 * StreetSafe Side Panel Script
 * Displays detailed crime statistics and methodology
 */

'use strict';

let currentData = null;

/**
 * Initialize the side panel
 */
async function initialize() {
  console.log('[StreetSafe Side Panel] Initializing...');

  // Request current data from background script
  try {
    const response = await chrome.runtime.sendMessage({ type: 'GET_CURRENT_DATA' });
    if (response && response.data) {
      currentData = response.data;
      displayData(currentData);
    } else {
      showLoading();
    }
  } catch (error) {
    console.error('[StreetSafe Side Panel] Error getting data:', error);
    showLoading();
  }

  // Listen for data updates
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === 'DATA_UPDATED') {
      currentData = message.data;
      displayData(currentData);
    }
  });

  // Attach event listeners
  attachEventListeners();
}

/**
 * Display crime statistics data
 * @param {Object} data - Crime statistics data
 */
function displayData(data) {
  if (!data || !data.stats) {
    showError();
    return;
  }

  // Hide loading/error, show data
  document.getElementById('loading-state').style.display = 'none';
  document.getElementById('error-state').style.display = 'none';
  document.getElementById('data-display').style.display = 'block';

  const { location, stats } = data;
  const { geography, metrics, timeWindow, dataThrough, comparisons } = stats;

  // Display location info
  displayLocationInfo(location, geography);

  // Display metrics
  displayMetrics(metrics);

  // Display comparisons
  displayComparisons(comparisons);

  // Update data date
  document.getElementById('data-date').textContent = new Date(dataThrough).toLocaleDateString();

  // Set time window
  document.getElementById('time-window').value = timeWindow || '12m';
}

/**
 * Display location information
 * @param {Object} location - Location data
 * @param {Object} geography - Geography data
 */
function displayLocationInfo(location, geography) {
  const container = document.getElementById('location-info');
  container.innerHTML = `
    <div class="location-info">
      <strong>Address:</strong> ${location.address || 'Unknown'}
      <br>
      <strong>Coordinates:</strong> ${location.lat.toFixed(6)}, ${location.lon.toFixed(6)}
      <br>
      <strong>Neighborhood:</strong> ${geography.ntaName || geography.ntaId}
      <br>
      <strong>Borough:</strong> ${geography.borough}
      <br>
      <strong>Source:</strong> ${formatSource(location.source)}
    </div>
  `;
}

/**
 * Display crime metrics
 * @param {Object} metrics - Metrics data
 */
function displayMetrics(metrics) {
  const container = document.getElementById('metrics-container');
  container.innerHTML = '';

  Object.entries(metrics).forEach(([key, metric]) => {
    const card = createMetricCard(key, metric);
    container.appendChild(card);
  });
}

/**
 * Create a metric card element
 * @param {string} key - Metric key
 * @param {Object} metric - Metric data
 * @returns {HTMLElement}
 */
function createMetricCard(key, metric) {
  const card = document.createElement('div');
  card.className = 'metric-card';

  const colorClass = getPercentileColorClass(metric.percentile);
  const percentileText = `Safer than ${Math.round(metric.percentile)}% of NYC`;

  card.innerHTML = `
    <div class="metric-header">
      <span class="metric-name">${formatMetricName(key)}</span>
      <span class="percentile-badge ${colorClass}">${percentileText}</span>
    </div>
    <div class="metric-stats">
      <div class="stat-item">
        <span class="stat-label">Count</span>
        <span class="stat-value">${formatNumber(metric.count)}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">Rate (per 100k)</span>
        <span class="stat-value">${formatRate(metric.rate)}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">Rank</span>
        <span class="stat-value">${metric.rank} / ${metric.total}</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">Percentile</span>
        <span class="stat-value">${Math.round(metric.percentile)}%</span>
      </div>
    </div>
  `;

  return card;
}

/**
 * Display comparison data
 * @param {Object} comparisons - Comparison data
 */
function displayComparisons(comparisons) {
  const container = document.getElementById('comparisons-container');
  container.innerHTML = '';

  if (!comparisons) return;

  // NYC averages
  if (comparisons.nycAverage) {
    Object.entries(comparisons.nycAverage).forEach(([key, value]) => {
      const item = document.createElement('div');
      item.className = 'comparison-item';
      item.innerHTML = `
        <span class="comparison-label">NYC Average (${formatMetricName(key)}):</span>
        <span class="comparison-value">${formatRate(value)} per 100k</span>
      `;
      container.appendChild(item);
    });
  }

  // Borough averages
  if (comparisons.boroughAverage) {
    Object.entries(comparisons.boroughAverage).forEach(([key, value]) => {
      const item = document.createElement('div');
      item.className = 'comparison-item';
      item.innerHTML = `
        <span class="comparison-label">Borough Average (${formatMetricName(key)}):</span>
        <span class="comparison-value">${formatRate(value)} per 100k</span>
      `;
      container.appendChild(item);
    });
  }
}

/**
 * Show loading state
 */
function showLoading() {
  document.getElementById('loading-state').style.display = 'block';
  document.getElementById('data-display').style.display = 'none';
  document.getElementById('error-state').style.display = 'none';
}

/**
 * Show error state
 */
function showError() {
  document.getElementById('loading-state').style.display = 'none';
  document.getElementById('data-display').style.display = 'none';
  document.getElementById('error-state').style.display = 'block';
}

/**
 * Attach event listeners
 */
function attachEventListeners() {
  // Time window selector
  const timeWindowSelect = document.getElementById('time-window');
  if (timeWindowSelect) {
    timeWindowSelect.addEventListener('change', (e) => {
      handleTimeWindowChange(e.target.value);
    });
  }
}

/**
 * Handle time window change
 * @param {string} window - New time window
 */
function handleTimeWindowChange(window) {
  console.log('[StreetSafe Side Panel] Time window changed to:', window);

  // Show loading
  showLoading();

  // Send message to background to update content script
  chrome.runtime.sendMessage({
    type: 'CHANGE_TIME_WINDOW',
    window: window
  });
}

/**
 * Format metric name for display
 * @param {string} key - Metric key
 * @returns {string}
 */
function formatMetricName(key) {
  const names = {
    murder: 'Murder',
    felonyAssault: 'Felony Assault',
    violentCrime: 'Violent Crime Index'
  };
  return names[key] || key;
}

/**
 * Format source for display
 * @param {string} source - Source identifier
 * @returns {string}
 */
function formatSource(source) {
  const sources = {
    map_image: 'Map image URL',
    structured_data: 'Page structured data',
    geocoded: 'NYC GeoSearch geocoding'
  };
  return sources[source] || source;
}

/**
 * Format number with commas
 * @param {number} num - Number to format
 * @returns {string}
 */
function formatNumber(num) {
  return num.toLocaleString('en-US');
}

/**
 * Format rate per 100k
 * @param {number} rate - Rate to format
 * @returns {string}
 */
function formatRate(rate) {
  return rate.toFixed(1);
}

/**
 * Get color class based on percentile
 * @param {number} percentile - Percentile (0-100)
 * @returns {string}
 */
function getPercentileColorClass(percentile) {
  if (percentile >= 75) return 'percentile-high';
  if (percentile >= 50) return 'percentile-medium';
  if (percentile >= 25) return 'percentile-low';
  return 'percentile-very-low';
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initialize);
} else {
  initialize();
}

console.log('[StreetSafe Side Panel] Script loaded');
