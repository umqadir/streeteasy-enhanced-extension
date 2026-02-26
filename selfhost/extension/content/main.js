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
  let removalObserver = null;

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
    if (!CoordinatesExtractor.isListingPage()) {
      return;
    }

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
        isProcessing = false;
        return;
      }

      // Watch for React hydration removing our container
      watchForRemoval();

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
   * Use a MutationObserver to detect when our injected container is removed
   * from the DOM (e.g., by React hydration rebuilding the page tree).
   * Re-injects automatically when that happens.
   *
   * Observes document.body with subtree: true because React can replace any
   * ancestor node — watching only the immediate parent misses grandparent
   * replacements.
   */
  function watchForRemoval() {
    if (removalObserver) removalObserver.disconnect();
    if (!uiInjector?.container) return;

    removalObserver = new MutationObserver(() => {
      if (uiInjector?.container && !document.contains(uiInjector.container)) {
        removalObserver.disconnect();
        removalObserver = null;
        // Reset injector state and re-inject
        uiInjector.isInjected = false;
        uiInjector.container = null;
        uiInjector.shadowRoot = null;
        isProcessing = false;
        setTimeout(initialize, 300);
      }
    });

    removalObserver.observe(document.body, { childList: true, subtree: true });
  }

  /**
   * Clean up when navigating away
   */
  function cleanup() {
    if (removalObserver) {
      removalObserver.disconnect();
      removalObserver = null;
    }
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
    setTimeout(initialize, 500);
  }

  /**
   * Handle measure change (pure UI transform; does not refetch).
   * @param {string} measure
   */
  function handleMeasureChange(measure) {
    currentMeasure = measure;
    if (!uiInjector) return;
    if (!currentLocationData || !currentStats) {
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

  // SPA navigation detection.
  // navigation-hook.js (runs in MAIN world) intercepts the page's pushState/
  // replaceState and dispatches __sleepEasyNav on window. We also listen for
  // popstate (back/forward button).
  window.addEventListener('popstate', () => handleNavigationChange());
  window.addEventListener('__sleepEasyNav', () => handleNavigationChange());

})();
