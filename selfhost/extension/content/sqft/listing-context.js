/**
 * SleepEasy Area Analysis - Listing Context Extractor
 *
 * Extracts listing ID, address, and photo elements from the StreetEasy DOM.
 * Does not download or cache any images.
 */

(function () {
  'use strict';

  // StreetEasy listing photos are served from Zillow's CDN.
  // The carousel uses CSS-module classes like MediaCarousel_*.
  const PHOTO_HOSTS = ['photos.zillowstatic.com', 'images.streeteasy.com', 'strt.ly'];
  const CAROUSEL_CLASS_RE = /MediaCarousel/;
  const MIN_PHOTO_SIZE = 150; // px — skip thumbnails and icons

  // Resolution suffix pattern: matches "-se_medium_500", "-se_large_800_400",
  // "-se_large_800_400.webp", etc. at end of pathname (before extension).
  const RESOLUTION_SUFFIX_RE = /-se_[a-z]+_\d+(?:_\d+)?/;

  class ListingContext {
    constructor() {
      this._photoObserver = null;
    }

    /**
     * Derive a stable listing ID from the current URL.
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
     * Extract listing address from the page.
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

      // Fallback: parse document title ("... at <address> in <neighborhood>")
      const title = document.title || '';
      const m = title.match(/\bat\s+([^|:]+?)\s+in\s+/i);
      if (m && m[1]) {
        const c = m[1].trim().replace(/\s+/g, ' ');
        if (c.length > 5 && c.length < 200 && /\d/.test(c)) return c;
      }

      // Fallback: title before " in " (e.g., "245 East 63rd Street #1010 in Lenox Hill")
      const m2 = title.match(/^(.+?)\s+in\s+/i);
      if (m2 && m2[1]) {
        const c = m2[1].trim().replace(/\s+/g, ' ');
        if (c.length > 5 && c.length < 200 && /\d/.test(c)) return c;
      }

      return null;
    }

    /**
     * Check if an image src is a listing photo (not an icon, logo, or map tile).
     * @param {string} src
     * @returns {boolean}
     */
    _isListingPhoto(src) {
      if (!src) return false;
      try {
        const u = new URL(src);
        return PHOTO_HOSTS.some(h => u.hostname === h || u.hostname.endsWith('.' + h));
      } catch {
        return false;
      }
    }

    /**
     * Build a map from photo base hash to carousel position by reading
     * the thumbnail strip. Thumbnails have labels like
     * "Image preview button, 3 of 11" and small img elements.
     *
     * @returns {{positionMap: Map<string, number>, totalPhotos: number}}
     */
    _buildPositionMap() {
      const positionMap = new Map();
      let totalPhotos = 0;

      // StreetEasy thumbnail buttons inside the carousel thumbs strip
      const thumbButtons = document.querySelectorAll(
        '[class*="MediaCarouselThumbs_thumbSlide"] button, ' +
        '[class*="mediaCarouselThumbs"] button'
      );

      for (const btn of thumbButtons) {
        // Label like "Image preview button, 3 of 11"
        const label = btn.textContent || btn.getAttribute('aria-label') || '';
        const match = label.match(/(\d+)\s+of\s+(\d+)/);
        if (!match) continue;

        const position = parseInt(match[1], 10);
        const total = parseInt(match[2], 10);
        if (total > totalPhotos) totalPhotos = total;

        // Find the thumbnail img
        const img = btn.querySelector('img');
        if (!img || !img.src) continue;

        const baseHash = this._getPhotoBaseHash(img.src);
        if (baseHash) {
          positionMap.set(baseHash, position);
        }
      }

      // Fallback: pagination text like "3 of 11" in the carousel
      if (totalPhotos === 0) {
        const paginationEl = document.querySelector('[class*="mediaCarouselPagination"]');
        if (paginationEl) {
          const m = (paginationEl.textContent || '').match(/(\d+)\s+of\s+(\d+)/);
          if (m) totalPhotos = parseInt(m[2], 10);
        }
      }

      return { positionMap, totalPhotos };
    }

    /**
     * Extract the base hash from a photo URL by stripping resolution suffix
     * and file extension. This allows matching between thumbnail URLs
     * (e.g., .../fp/HASH-se_medium_500) and carousel URLs
     * (e.g., .../fp/HASH-se_large_800_400.webp).
     *
     * @param {string} url
     * @returns {string|null}
     */
    _getPhotoBaseHash(url) {
      if (!url) return null;
      try {
        const u = new URL(url);
        // Strip query/hash
        let path = u.pathname;
        // Strip file extension (.webp, .jpg, etc.)
        path = path.replace(/\.[a-z]+$/i, '');
        // Strip resolution suffix (-se_medium_500, -se_large_800_400, etc.)
        path = path.replace(RESOLUTION_SUFFIX_RE, '');
        return `${u.hostname}${path}`;
      } catch {
        return null;
      }
    }

    /**
     * Get the current carousel position from the pagination element.
     * @returns {{current: number, total: number} | null}
     */
    getCurrentCarouselPosition() {
      const paginationEl = document.querySelector('[class*="mediaCarouselPagination"]');
      if (!paginationEl) return null;
      const m = (paginationEl.textContent || '').match(/(\d+)\s+of\s+(\d+)/);
      if (!m) return null;
      return { current: parseInt(m[1], 10), total: parseInt(m[2], 10) };
    }

    /**
     * Find all listing photo elements in the carousel.
     * @returns {Array<{element: HTMLElement, url: string, index: number, position: number|null, totalPhotos: number}>}
     */
    getPhotoElements() {
      const results = [];
      const seen = new Set();
      const { positionMap, totalPhotos } = this._buildPositionMap();

      // Prefer a stable carousel root; StreetEasy has changed CSS-module classnames
      // over time, but data-testid has stayed relatively stable.
      const carouselRoot = document.querySelector('[data-testid="media-carousel-component"]')
        || document.querySelector('[class*="MediaCarousel_mediaCarousel"]')
        || document.querySelector('[class*="MediaCarousel"]');

      // Find images that are likely to be the main listing photos in the carousel.
      const allImgs = document.querySelectorAll('img');
      for (const img of allImgs) {
        if (!this._isListingPhoto(img.src)) continue;

        // Size filter: skip thumbnails, icons, logos
        const w = img.width || img.naturalWidth || 0;
        const h = img.height || img.naturalHeight || 0;
        if (w > 0 && w < MIN_PHOTO_SIZE && h > 0 && h < MIN_PHOTO_SIZE) continue;

        // Check that it's in the carousel (not a hero image elsewhere, map overlay, etc.)
        if (carouselRoot) {
          if (!carouselRoot.contains(img)) continue;
        } else {
          // Fallback: match legacy CSS-module fragments.
          const legacy = img.closest('[class*="MediaCarousel"]');
          if (!legacy) continue;
        }

        const url = this._normalizePhotoUrl(img.src);
        if (!url || seen.has(url)) continue;
        seen.add(url);

        // Look up position from thumbnail mapping
        const baseHash = this._getPhotoBaseHash(img.src);
        const position = baseHash ? (positionMap.get(baseHash) ?? null) : null;

        results.push({ element: img, url, index: results.length, position, totalPhotos });
      }

      return results;
    }

    /**
     * Get the overlay container for a photo element.
     * On StreetEasy, carousel photos sit inside a BUTTON with position:relative.
     * @param {HTMLElement} photoElement
     * @returns {HTMLElement}
     */
    getPhotoContainer(photoElement) {
      // Newer carousel markup wraps the main photo in a button with aria-label like "photo 1".
      const photoBtn = photoElement.closest('button[aria-label^="photo"]');
      if (photoBtn) return photoBtn;

      // The immediate carousel image container (BUTTON with position:relative)
      const container = photoElement.closest('[class*="MediaCarousel_mediaCarouselImageContainer"]');
      if (container) return container;

      // Generic fallback: walk up to first positioned element
      let el = photoElement.parentElement;
      while (el && el !== document.body) {
        const pos = getComputedStyle(el).position;
        if (pos === 'relative' || pos === 'absolute') return el;
        el = el.parentElement;
      }

      return photoElement.parentElement || photoElement;
    }

    /**
     * Watch for carousel changes (slide navigation, lazy loading).
     * Uses a targeted observer on the carousel container rather than the whole document.
     * @param {function} callback
     * @returns {function} Cleanup function
     */
    observePhotoChanges(callback) {
      if (this._photoObserver) {
        this._photoObserver.disconnect();
      }

      // Find the carousel root to scope observation
      const carouselRoot = document.querySelector('[data-testid="media-carousel-component"]')
        || document.querySelector('[class*="MediaCarousel_mediaCarousel"]')
        || document.querySelector('[class*="MediaCarousel"]');

      // Fall back to body if carousel not found yet (may render later)
      const target = carouselRoot || document.body;

      this._photoObserver = new MutationObserver(callback);
      this._photoObserver.observe(target, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['src'],
      });

      return () => {
        if (this._photoObserver) {
          this._photoObserver.disconnect();
          this._photoObserver = null;
        }
      };
    }

    /**
     * Normalize a photo URL for stable storage keys.
     * Strips query params and hash.
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

    cleanup() {
      if (this._photoObserver) {
        this._photoObserver.disconnect();
        this._photoObserver = null;
      }
    }
  }

  window.SleepEasyListingContext = ListingContext;
})();
