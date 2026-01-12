/**
 * SleepEasy Main Content Script
 * Orchestrates coordinate extraction, data fetching, and UI injection
 */

(function() {
  'use strict';

  let uiInjector = null;
  let currentPageKey = getPageKey();
  let isProcessing = false;
  let currentMeasure = CONFIG.DEFAULT_MEASURE;
  let currentLocationData = null;
  let currentStats = null;

  function getPageKey() {
    try {
      const url = new URL(window.location.href);
      return `${url.origin}${url.pathname}`;
    } catch {
      return window.location.pathname || window.location.href;
    }
  }

  /**
   * Initialize SleepEasy on the current page
   */
  async function initialize() {
    // Check if this is a listing page
    if (!CoordinatesExtractor.isListingPage()) {
      return;
    }

    // Avoid processing the same page multiple times
    if (isProcessing) {
      return;
    }

    isProcessing = true;

    try {
      // Step 1: Show loading UI immediately
      uiInjector = uiInjector || new UIInjector({
        onMeasureChange: handleMeasureChange
      });
      const injected = uiInjector.inject(null); // null = loading state

      if (!injected) {
        // If we can't inject cleanly, avoid doing work on the page.
        isProcessing = false;
        return;
      }

      // Step 2: Extract coordinates
      const extractor = new CoordinatesExtractor();
      const locationData = await extractor.extract();

      if (!locationData) {
        currentLocationData = null;
        uiInjector?.update({ error: 'Crime unavailable.' });
        isProcessing = false;
        return;
      }

      currentLocationData = locationData;

      // Step 3: Fetch crime statistics
      const stats = await fetchCrimeStats(
        locationData.lat,
        locationData.lon,
        CONFIG.DEFAULT_TIME_WINDOW
      );

      if (!stats) {
        currentStats = null;
        uiInjector?.update({ error: 'Crime unavailable.' });
        isProcessing = false;
        return;
      }

      currentStats = stats;

      // Step 4: Update UI with data
      uiInjector?.update({ location: locationData, stats, uiState: { measure: currentMeasure } });

    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error during initialization:', error);
      currentStats = null;
      uiInjector?.update({ error: 'Crime unavailable.' });
    } finally {
      isProcessing = false;
    }
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
    currentLocationData = null;
    currentStats = null;
  }

  /**
   * Handle URL changes (for SPA navigation)
   */
  function handleNavigationChange() {
    const newKey = getPageKey();
    if (newKey === currentPageKey) return;
    currentPageKey = newKey;
    cleanup();
    setTimeout(initialize, 500); // Give the page time to render
  }

  /**
   * Handle measure change (pure UI transform; does not refetch).
   * @param {string} measure
   */
  function handleMeasureChange(measure) {
    currentMeasure = measure;
    if (!uiInjector) return;
    if (!currentLocationData || !currentStats) {
      // If we don't have data yet, just keep the selection for the next render.
      return;
    }
    uiInjector.update({ location: currentLocationData, stats: currentStats, uiState: { measure: currentMeasure } });
  }

  // Initialize on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }

  // Watch for URL changes (SPA navigation)
  setInterval(handleNavigationChange, 1000);

  // Also listen for history API changes
  window.addEventListener('popstate', () => {
    handleNavigationChange();
  });

})();
