/**
 * SleepEasy main content script.
 * Orchestrates coordinate extraction, crime-stats lookup, and UI injection.
 */

(function() {
  'use strict';

  let uiInjector = null;
  let currentPageKey = getPageKey();
  let currentMeasure = CONFIG.DEFAULT_MEASURE;
  let currentTimeWindow = CONFIG.DEFAULT_TIME_WINDOW;
  let currentLocationData = null;
  let currentStats = null;
  let removalObserver = null;

  // Monotonic token: each (re)initialization bumps it, and async results from
  // a superseded run are discarded instead of clobbering the current page.
  let runToken = 0;

  function getPageKey() {
    try {
      const url = new URL(window.location.href);
      return `${url.origin}${url.pathname}`;
    } catch {
      return window.location.pathname || window.location.href;
    }
  }

  async function initialize() {
    if (!CoordinatesExtractor.isListingPage()) return;

    const token = ++runToken;

    try {
      // Show loading state immediately
      uiInjector = uiInjector || new UIInjector({
        onMeasureChange: handleMeasureChange,
        onTimeWindowChange: handleTimeWindowChange
      });
      if (!uiInjector.inject(null)) return;

      // React hydration can replace our container's ancestors — watch and re-inject.
      watchForRemoval();

      const extractor = new CoordinatesExtractor();
      const locationData = await extractor.extract();
      if (token !== runToken) return; // superseded by navigation

      if (!locationData) {
        currentLocationData = null;
        uiInjector?.update({ error: 'Crime data unavailable for this listing.' });
        return;
      }
      currentLocationData = locationData;

      const stats = await fetchCrimeStats(locationData.lat, locationData.lon, currentTimeWindow);
      if (token !== runToken) return;

      if (!stats) {
        currentStats = null;
        uiInjector?.update({ error: 'Crime data unavailable for this listing.' });
        return;
      }
      currentStats = stats;

      uiInjector?.update({
        location: locationData,
        stats,
        uiState: { measure: currentMeasure, timeWindow: currentTimeWindow }
      });
    } catch (error) {
      SleepEasyLog.error('[SleepEasy] Error during initialization:', error);
      if (token === runToken) {
        currentStats = null;
        uiInjector?.update({ error: 'Crime data unavailable for this listing.' });
      }
    }
  }

  /**
   * Detect when React hydration removes our injected container and re-inject.
   * Observes document.body with subtree because React can replace any ancestor.
   */
  function watchForRemoval() {
    if (removalObserver) removalObserver.disconnect();
    if (!uiInjector?.container) return;

    removalObserver = new MutationObserver(() => {
      if (uiInjector?.container && !document.contains(uiInjector.container)) {
        removalObserver.disconnect();
        removalObserver = null;
        uiInjector.reset();
        setTimeout(initialize, 300);
      }
    });

    removalObserver.observe(document.body, { childList: true, subtree: true });
  }

  function cleanup() {
    runToken++;
    if (removalObserver) {
      removalObserver.disconnect();
      removalObserver = null;
    }
    if (uiInjector) {
      uiInjector.remove();
      uiInjector = null;
    }
    currentLocationData = null;
    currentStats = null;
  }

  function handleNavigationChange() {
    const newKey = getPageKey();
    if (newKey === currentPageKey) return;
    currentPageKey = newKey;
    cleanup();
    setTimeout(initialize, 500);
  }

  /** Measure change is a pure UI transform — no refetch. */
  function handleMeasureChange(measure) {
    currentMeasure = measure;
    if (!uiInjector || !currentLocationData || !currentStats) return;
    uiInjector.update({
      location: currentLocationData,
      stats: currentStats,
      uiState: { measure: currentMeasure, timeWindow: currentTimeWindow }
    });
  }

  /** Time-window change re-derives stats from the bundled data (still local). */
  async function handleTimeWindowChange(timeWindow) {
    currentTimeWindow = timeWindow;
    if (!uiInjector || !currentLocationData) return;

    const token = runToken;
    const stats = await fetchCrimeStats(currentLocationData.lat, currentLocationData.lon, timeWindow);
    if (token !== runToken) return;

    if (stats) {
      currentStats = stats;
      uiInjector.update({
        location: currentLocationData,
        stats,
        uiState: { measure: currentMeasure, timeWindow: currentTimeWindow }
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }

  // SPA navigation: navigation-hook.js (MAIN world) re-dispatches pushState/
  // replaceState as __sleepEasyNav; popstate covers back/forward.
  window.addEventListener('popstate', () => handleNavigationChange());
  window.addEventListener('__sleepEasyNav', () => handleNavigationChange());
})();
