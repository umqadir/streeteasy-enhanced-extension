/**
 * SleepEasy Area Analysis - Listing Context Extractor
 *
 * Extracts listing ID, address, and photo elements from the StreetEasy DOM.
 * Does not download or cache any images.
 */

(function () {
  'use strict';

  class ListingContext {
    constructor() {
      this._photoObserver = null;
    }

    /**
     * Derive a stable listing ID from the current URL.
     * e.g., "/rental/12345" -> "rental:12345"
     *       "/building/some-building/unit-7a" -> "building:some-building:unit-7a"
     * @returns {string|null}
     */
    getListingId() {
      const path = window.location.pathname;
      const match = path.match(/^\/(building|rental|sale)\/(.+?)(?:\/)?$/);
      if (!match) return null;
      const [, type, rest] = match;
      return `${type}:${rest.replace(/\//g, ':')}`;
    }

    /** @returns {string} */
    getListingUrl() {
      try {
        const u = new URL(window.location.href);
        u.search = '';
        u.hash = '';
        return u.toString();
      } catch {
        return window.location.href;
      }
    }

    /**
     * Extract listing address from the page. Mirrors CoordinatesExtractor logic.
     * @returns {string|null}
     */
    getAddress() {
      const selectors = [
        '[data-testid="listing-address"]',
        '.listing-address',
        '.building-title',
        'h1[class*="address"]',
        '[class*="Address"]',
        '[itemprop="address"]',
        'address',
      ];

      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (!el) continue;
        let text = el.textContent.trim().replace(/\s+/g, ' ');
        if (text.length > 5 && text.length < 200 && /\d/.test(text)) {
          return text;
        }
      }

      // Fallback: parse document title
      const title = document.title || '';
      const m = title.match(/\bat\s+([^|:]+?)\s+in\s+/i);
      if (m && m[1]) {
        const c = m[1].trim().replace(/\s+/g, ' ');
        if (c.length > 5 && c.length < 200 && /\d/.test(c)) return c;
      }

      return null;
    }

    /**
     * Find all photo elements currently visible in the listing gallery/carousel.
     * Returns DOM elements and their source URLs.
     *
     * StreetEasy photo galleries typically use:
     * - <img> elements inside a carousel/slider container
     * - CSS background-image on divs
     * - Image URLs from images.streeteasy.com CDN
     *
     * @returns {Array<{element: HTMLElement, url: string, index: number}>}
     */
    getPhotoElements() {
      const results = [];
      const seen = new Set();

      // Strategy 1: Find img elements with StreetEasy CDN URLs
      const imgs = document.querySelectorAll('img[src*="streeteasy.com"], img[src*="strt.ly"]');
      for (const img of imgs) {
        const url = this._normalizePhotoUrl(img.src);
        if (!url || seen.has(url)) continue;
        // Filter out tiny icons/logos (listing photos are typically > 200px)
        if (img.naturalWidth > 0 && img.naturalWidth < 100) continue;
        if (img.width > 0 && img.width < 100) continue;
        seen.add(url);
        results.push({ element: img, url, index: results.length });
      }

      // Strategy 2: Background images on divs (some carousels use this)
      const divs = document.querySelectorAll('[style*="background-image"]');
      for (const div of divs) {
        const style = div.style.backgroundImage || '';
        const urlMatch = style.match(/url\(["']?(https?:\/\/[^"')]+streeteasy[^"')]+)["']?\)/);
        if (!urlMatch) continue;
        const url = this._normalizePhotoUrl(urlMatch[1]);
        if (!url || seen.has(url)) continue;
        seen.add(url);
        results.push({ element: div, url, index: results.length });
      }

      // Strategy 3: data-src or srcset attributes (lazy-loaded images)
      const lazyImgs = document.querySelectorAll('[data-src*="streeteasy.com"], [data-src*="strt.ly"]');
      for (const img of lazyImgs) {
        const src = img.dataset.src || '';
        const url = this._normalizePhotoUrl(src);
        if (!url || seen.has(url)) continue;
        seen.add(url);
        results.push({ element: img, url, index: results.length });
      }

      return results;
    }

    /**
     * Get the closest photo container (carousel wrapper) for overlay positioning.
     * @param {HTMLElement} photoElement
     * @returns {HTMLElement}
     */
    getPhotoContainer(photoElement) {
      // Walk up to find a container that is position:relative or has gallery/carousel class
      let el = photoElement.parentElement;
      while (el && el !== document.body) {
        const style = window.getComputedStyle(el);
        // A positioned container (relative, absolute, fixed) is a valid overlay parent
        if (style.position === 'relative' || style.position === 'absolute') {
          return el;
        }
        // Also check for common carousel container patterns
        const cls = el.className || '';
        if (/carousel|gallery|slider|photo|media|image/i.test(cls)) {
          return el;
        }
        el = el.parentElement;
      }
      // Fallback: use the photo element's direct parent
      return photoElement.parentElement || photoElement;
    }

    /**
     * Watch for new photos being added to the DOM (lazy loading, carousel navigation).
     * @param {function} callback - Called when photo elements change
     * @returns {function} Cleanup function to disconnect observer
     */
    observePhotoChanges(callback) {
      if (this._photoObserver) {
        this._photoObserver.disconnect();
      }

      this._photoObserver = new MutationObserver(() => {
        callback();
      });

      // Observe the entire document for img/div additions
      this._photoObserver.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['src', 'data-src', 'style'],
      });

      return () => {
        if (this._photoObserver) {
          this._photoObserver.disconnect();
          this._photoObserver = null;
        }
      };
    }

    /**
     * Normalize a photo URL for use as a stable storage key.
     * Strips query params, hash, and trailing size suffixes.
     * @param {string} url
     * @returns {string|null}
     */
    _normalizePhotoUrl(url) {
      if (!url) return null;
      try {
        const u = new URL(url);
        u.search = '';
        u.hash = '';
        return u.toString();
      } catch {
        return null;
      }
    }

    /** Clean up observers */
    cleanup() {
      if (this._photoObserver) {
        this._photoObserver.disconnect();
        this._photoObserver = null;
      }
    }
  }

  window.SleepEasyListingContext = ListingContext;
})();
