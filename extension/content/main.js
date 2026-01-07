/**
 * StreetSafe Main Content Script
 * Orchestrates coordinate extraction, data fetching, and UI injection
 */

(function() {
  'use strict';

  let uiInjector = null;
  let currentUrl = window.location.href;
  let isProcessing = false;

  /**
   * Initialize StreetSafe on the current page
   */
  async function initialize() {
    console.log('[StreetSafe] Initializing...');

    // Check if this is a listing page
    if (!CoordinatesExtractor.isListingPage()) {
      console.log('[StreetSafe] Not a listing page, skipping');
      return;
    }

    // Avoid processing the same page multiple times
    if (isProcessing) {
      console.log('[StreetSafe] Already processing, skipping');
      return;
    }

    isProcessing = true;

    try {
      // Step 1: Show loading UI immediately
      uiInjector = new UIInjector();
      const injected = uiInjector.inject(null); // null = loading state

      if (!injected) {
        console.warn('[StreetSafe] Could not inject inline UI, will use side panel fallback');
        // Could open side panel automatically here, but let's keep it user-initiated
      }

      // Step 2: Extract coordinates
      const extractor = new CoordinatesExtractor();
      const locationData = await extractor.extract();

      if (!locationData) {
        console.error('[StreetSafe] Could not extract location');
        uiInjector?.update({ error: 'Could not determine listing location' });
        isProcessing = false;
        return;
      }

      console.log('[StreetSafe] Location extracted:', locationData);

      // Step 3: Fetch crime statistics
      const stats = await fetchCrimeStatsWithCache(
        locationData.lat,
        locationData.lon,
        CONFIG.DEFAULT_TIME_WINDOW
      );

      if (!stats) {
        console.error('[StreetSafe] Could not fetch crime statistics');
        uiInjector?.update({ error: 'Could not load crime statistics' });
        isProcessing = false;
        return;
      }

      console.log('[StreetSafe] Crime statistics loaded:', stats);

      // Step 4: Update UI with data
      uiInjector?.update(stats);

      // Store current location in extension storage for side panel
      chrome.runtime.sendMessage({
        type: 'LOCATION_UPDATED',
        data: {
          location: locationData,
          stats: stats
        }
      });

    } catch (error) {
      console.error('[StreetSafe] Error during initialization:', error);
      uiInjector?.update({ error: 'An error occurred loading crime statistics' });
    } finally {
      isProcessing = false;
    }
  }

  /**
   * Fetch crime statistics with caching
   * @param {number} lat - Latitude
   * @param {number} lon - Longitude
   * @param {string} window - Time window
   * @returns {Promise<Object|null>}
   */
  async function fetchCrimeStatsWithCache(lat, lon, window) {
    const cacheKey = `crime_stats_${lat}_${lon}_${window}`;

    // Try cache first
    const cached = await Cache.get(cacheKey);
    if (cached) {
      console.log('[StreetSafe] Using cached data');
      return cached;
    }

    // Fetch fresh data
    const stats = await fetchCrimeStats(lat, lon, window);
    if (stats) {
      await Cache.set(cacheKey, stats);
    }

    return stats;
  }

  /**
   * Clean up when navigating away
   */
  function cleanup() {
    if (uiInjector) {
      uiInjector.remove();
      uiInjector = null;
    }
    isProcessing = false;
  }

  /**
   * Handle URL changes (for SPA navigation)
   */
  function checkUrlChange() {
    const newUrl = window.location.href;
    if (newUrl !== currentUrl) {
      console.log('[StreetSafe] URL changed, reinitializing');
      currentUrl = newUrl;
      cleanup();
      setTimeout(initialize, 500); // Give the page time to render
    }
  }

  /**
   * Listen for messages from background script
   */
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log('[StreetSafe] Received message:', message);

    switch (message.type) {
      case 'CHANGE_TIME_WINDOW':
        handleTimeWindowChange(message.window);
        break;
      case 'REINITIALIZE':
        cleanup();
        initialize();
        break;
    }

    return true; // Keep message channel open
  });

  /**
   * Handle time window change
   * @param {string} window - New time window
   */
  async function handleTimeWindowChange(window) {
    if (!uiInjector) return;

    // Show loading state
    uiInjector.update(null);

    // Re-extract location (should be fast from cache)
    const extractor = new CoordinatesExtractor();
    const locationData = await extractor.extract();

    if (!locationData) {
      uiInjector.update({ error: 'Could not determine listing location' });
      return;
    }

    // Fetch with new window
    const stats = await fetchCrimeStatsWithCache(
      locationData.lat,
      locationData.lon,
      window
    );

    if (!stats) {
      uiInjector.update({ error: 'Could not load crime statistics' });
      return;
    }

    // Update UI
    uiInjector.update(stats);
  }

  // Initialize on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }

  // Watch for URL changes (SPA navigation)
  setInterval(checkUrlChange, 1000);

  // Also listen for history API changes
  window.addEventListener('popstate', () => {
    console.log('[StreetSafe] History popstate detected');
    cleanup();
    setTimeout(initialize, 500);
  });

  console.log('[StreetSafe] Content script loaded');
})();
