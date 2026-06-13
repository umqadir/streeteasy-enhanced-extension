/**
 * SleepEasy Navigation Hook
 *
 * Runs in the PAGE's main world (not the extension's isolated world) so it can
 * intercept the page's own calls to history.pushState / replaceState.
 *
 * Dispatches a custom event on window whenever the pathname changes, which
 * content scripts in the isolated world can listen for.
 */
(function () {
  'use strict';

  var _pushState = history.pushState;
  var _replaceState = history.replaceState;

  function maybeDispatch(original, ctx, args) {
    var before = location.pathname;
    original.apply(ctx, args);
    if (location.pathname !== before) {
      window.dispatchEvent(new CustomEvent('__sleepEasyNav'));
    }
  }

  history.pushState = function () {
    maybeDispatch(_pushState, this, arguments);
  };

  history.replaceState = function () {
    maybeDispatch(_replaceState, this, arguments);
  };
})();
