/**
 * SleepEasy coordinates extractor.
 *
 * Resolves the listing's location from the StreetEasy page itself — no
 * geocoding, no network calls. Strategies, in order:
 *   1. JSON-LD structured data (schema.org geo coordinates), when present
 *   2. The listing's "View on Google Maps" link (the proven, stable source:
 *      https://www.google.com/maps/place/<lat>,<lon>)
 * Both are retried while the SPA renders.
 */

// NYC sanity bounds — reject parses that land outside the metro area.
const NYC_BOUNDS = { minLat: 40.45, maxLat: 41.0, minLon: -74.3, maxLon: -73.6 };

class CoordinatesExtractor {
  constructor() {
    this.coordinates = null;
    this.address = null;
    this.neighborhood = null;
  }

  /**
   * Extract coordinates from the page.
   * @returns {Promise<{lat, lon, source, address, neighborhood}|null>}
   */
  async extract() {
    const maxAttempts = 20;
    const delayMs = 150;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const found = this.extractFromJsonLd() || this.extractFromGoogleMapsLink();
      if (found) {
        this.coordinates = found;
        this.address = this.extractAddress();
        this.neighborhood = this.extractNeighborhood();
        return {
          lat: found.lat,
          lon: found.lon,
          source: found.source,
          address: this.address,
          neighborhood: this.neighborhood
        };
      }
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }

    SleepEasyLog.warn('[SleepEasy] Could not extract coordinates');
    return null;
  }

  _validCoords(lat, lon) {
    return Number.isFinite(lat) && Number.isFinite(lon)
      && lat >= NYC_BOUNDS.minLat && lat <= NYC_BOUNDS.maxLat
      && lon >= NYC_BOUNDS.minLon && lon <= NYC_BOUNDS.maxLon;
  }

  /**
   * Look for schema.org structured data with a geo block, e.g.
   * {"@type": "Residence", "geo": {"latitude": 40.77, "longitude": -73.95}}.
   * Walks nested objects/arrays since listings often wrap the geo in @graph.
   * @returns {{lat, lon, source}|null}
   */
  extractFromJsonLd() {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of scripts) {
      let parsed;
      try {
        parsed = JSON.parse(script.textContent);
      } catch {
        continue;
      }
      const geo = this._findGeo(parsed, 0);
      if (geo) return { ...geo, source: 'json_ld' };
    }
    return null;
  }

  _findGeo(node, depth) {
    if (!node || typeof node !== 'object' || depth > 6) return null;

    if (Array.isArray(node)) {
      for (const item of node) {
        const found = this._findGeo(item, depth + 1);
        if (found) return found;
      }
      return null;
    }

    const geo = node.geo;
    if (geo && typeof geo === 'object') {
      const lat = parseFloat(geo.latitude);
      const lon = parseFloat(geo.longitude);
      if (this._validCoords(lat, lon)) return { lat, lon };
    }

    const lat = parseFloat(node.latitude);
    const lon = parseFloat(node.longitude);
    if (this._validCoords(lat, lon)) return { lat, lon };

    for (const value of Object.values(node)) {
      if (value && typeof value === 'object') {
        const found = this._findGeo(value, depth + 1);
        if (found) return found;
      }
    }
    return null;
  }

  /**
   * Extract coordinates from a Google Maps link on the page.
   * @returns {{lat, lon, source}|null}
   */
  extractFromGoogleMapsLink() {
    const links = document.querySelectorAll('a[href*="google.com/maps"], a[href*="maps.google."]');

    for (const link of links) {
      const coords = this.parseCoordinatesFromGoogleMapsUrl(link.getAttribute('href'));
      if (coords) return { ...coords, source: 'google_maps_link' };
    }

    return null;
  }

  /**
   * Parse coordinates out of the URL shapes Google Maps links use.
   * @returns {{lat, lon}|null}
   */
  parseCoordinatesFromGoogleMapsUrl(href) {
    if (!href) return null;

    let url;
    try {
      url = new URL(href, window.location.href);
    } catch {
      return null;
    }

    const tryPair = (latStr, lonStr) => {
      const lat = parseFloat(latStr);
      const lon = parseFloat(lonStr);
      return this._validCoords(lat, lon) ? { lat, lon } : null;
    };

    // 1) /maps/place/<lat>,<lon>  (StreetEasy's "View on Google Maps")
    const placeMatch = url.pathname.match(/\/maps\/place\/(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
    if (placeMatch) {
      const c = tryPair(placeMatch[1], placeMatch[2]);
      if (c) return c;
    }

    // 1b) /maps/place/<address>/<lat>,<lon>,...
    const pathCoordsMatch = url.pathname.match(/(?:^|\/)(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)(?:,|$)/);
    if (pathCoordsMatch) {
      const c = tryPair(pathCoordsMatch[1], pathCoordsMatch[2]);
      if (c) return c;
    }

    // 2) /.../@<lat>,<lon>,...
    const atMatch = url.pathname.match(/@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
    if (atMatch) {
      const c = tryPair(atMatch[1], atMatch[2]);
      if (c) return c;
    }

    // 3) ?q= / ?query= / ?ll= / ?sll= / ?center=
    for (const param of ['q', 'query', 'll', 'sll', 'center']) {
      const value = url.searchParams.get(param);
      if (!value) continue;
      const m = value.match(/(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
      if (m) {
        const c = tryPair(m[1], m[2]);
        if (c) return c;
      }
    }

    return null;
  }

  /**
   * Extract the listing address from the page.
   * @returns {string|null}
   */
  extractAddress() {
    const selectors = [
      '[data-testid="listing-address"]',
      '.listing-address',
      '.building-title',
      'h1[class*="address"]',
      '[class*="Address"]',
      '[itemprop="address"]',
      'address'
    ];

    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) continue;
      const text = element.textContent.trim().replace(/\s+/g, ' ');
      // Prefer strings that look like a street address (contain a number)
      if (text.length > 5 && text.length < 200 && /\d/.test(text)) {
        return /,\s*NY\b/i.test(text) ? text : `${text}, New York, NY`;
      }
    }

    // Many listing titles read "... at <address> in <neighborhood> ..."
    const title = document.title || '';
    const titleMatch = title.match(/\bat\s+([^|:]+?)\s+in\s+/i);
    if (titleMatch && titleMatch[1]) {
      const candidate = titleMatch[1].trim().replace(/\s+/g, ' ');
      if (candidate.length > 5 && candidate.length < 200 && /\d/.test(candidate)) {
        return /,\s*NY\b/i.test(candidate) ? candidate : `${candidate}, New York, NY`;
      }
    }

    return null;
  }

  /**
   * StreetEasy's own neighborhood label, for display context only (the stats
   * lookup uses the official NTA from the coordinates).
   * @returns {string|null}
   */
  extractNeighborhood() {
    const title = document.title || '';
    const match = title.match(/\bin\s+([^:|]+)\s*[:|]/i);
    if (match && match[1]) {
      const name = match[1].trim();
      if (name && name.length < 80) return name;
    }

    const breadcrumb = document.querySelector('nav[aria-label="breadcrumb"], [aria-label="breadcrumb"]');
    if (breadcrumb) {
      const links = Array.from(breadcrumb.querySelectorAll('a'))
        .map(a => (a.textContent || '').trim())
        .filter(Boolean);
      if (links.length >= 2) {
        const candidate = links[links.length - 1];
        if (candidate && candidate.length < 80) return candidate;
      }
    }

    return null;
  }

  /**
   * @returns {boolean} whether the current page is a listing page
   */
  static isListingPage() {
    return /\/(building|rental|sale)\//.test(window.location.href);
  }
}

window.CoordinatesExtractor = CoordinatesExtractor;
