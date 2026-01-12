/**
 * SleepEasy Coordinates Extractor
 * Extracts listing location from StreetEasy pages
 */

class CoordinatesExtractor {
  constructor() {
    this.coordinates = null;
    this.address = null;
    this.neighborhood = null;
  }

  /**
   * Extract coordinates from the page
   * @returns {Promise<Object|null>} {lat, lon, source, address}
   */
  async extract() {
    // StreetEasy listing pages include a Google Maps link with coordinates.
    // This is the canonical source and avoids any need for geocoding.
    const googleCoords = await this.extractFromGoogleMapsLinkWithRetry();
    if (googleCoords) {
      this.coordinates = googleCoords;
      this.address = this.extractAddress();
      this.neighborhood = this.extractNeighborhood();
      return {
        ...googleCoords,
        source: 'google_maps_link',
        address: this.address,
        neighborhood: this.neighborhood
      };
    }

    SleepEasyLog.warn('[SleepEasy] Could not extract coordinates');
    return null;
  }

  /**
   * Retry extraction a few times to handle SPA async render.
   * @returns {Promise<Object|null>} {lat, lon}
   */
  async extractFromGoogleMapsLinkWithRetry() {
    const maxAttempts = 20;
    const delayMs = 150;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const coords = this.extractFromGoogleMapsLink();
      if (coords) return coords;
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }

    return null;
  }

  /**
   * Extract coordinates from a Google Maps link on the page.
   * Expected shape: https://www.google.com/maps/place/40.7206,-73.9878
   * @returns {Object|null} {lat, lon}
   */
  extractFromGoogleMapsLink() {
    const links = document.querySelectorAll('a[href*="google.com/maps"], a[href*="maps.google."]');

    for (const link of links) {
      const href = link.getAttribute('href');
      const coords = this.parseCoordinatesFromGoogleMapsUrl(href);
      if (coords) return coords;
    }

    return null;
  }

  /**
   * Parse coordinates from a Google Maps URL.
   * @param {string|null} href
   * @returns {Object|null} {lat, lon}
   */
  parseCoordinatesFromGoogleMapsUrl(href) {
    if (!href) return null;

    let url;
    try {
      url = new URL(href, window.location.href);
    } catch {
      return null;
    }

    // 1) /maps/place/<lat>,<lon>
    const placeMatch = url.pathname.match(/\/maps\/place\/(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
    if (placeMatch) {
      const lat = parseFloat(placeMatch[1]);
      const lon = parseFloat(placeMatch[2]);
      if (!isNaN(lat) && !isNaN(lon)) return { lat, lon };
    }

    // 2) /.../@<lat>,<lon>,...
    const atMatch = url.pathname.match(/@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
    if (atMatch) {
      const lat = parseFloat(atMatch[1]);
      const lon = parseFloat(atMatch[2]);
      if (!isNaN(lat) && !isNaN(lon)) return { lat, lon };
    }

    // 3) ?q=<lat>,<lon> (or variants)
    const q = url.searchParams.get('q') || url.searchParams.get('query');
    if (q) {
      const qMatch = q.match(/(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
      if (qMatch) {
        const lat = parseFloat(qMatch[1]);
        const lon = parseFloat(qMatch[2]);
        if (!isNaN(lat) && !isNaN(lon)) return { lat, lon };
      }
    }

    // 4) ?ll=<lat>,<lon> (embedded Google Maps uses this)
    const ll = url.searchParams.get('ll') || url.searchParams.get('sll') || url.searchParams.get('center');
    if (ll) {
      const llMatch = ll.match(/(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
      if (llMatch) {
        const lat = parseFloat(llMatch[1]);
        const lon = parseFloat(llMatch[2]);
        if (!isNaN(lat) && !isNaN(lon)) return { lat, lon };
      }
    }

    return null;
  }

  /**
   * Extract the listing address from the page
   * @returns {string|null} Address string
   */
  extractAddress() {
    // Try multiple selectors for address
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
      if (element) {
        let text = element.textContent.trim();
        // Clean up the address
        text = text.replace(/\s+/g, ' ').trim();
        if (text.length > 5 && text.length < 200) {
          // Basic heuristic: prefer strings that look like a street address (contain a number)
          if (/\d/.test(text)) {
            if (!/,\s*NY\b/i.test(text)) {
              text += ', New York, NY';
            }
            return text;
          }
        }
      }
    }

    // Many building/listing titles include "... at <address> in <neighborhood> ..."
    const title = document.title || '';
    const titleMatch = title.match(/\bat\s+([^|:]+?)\s+in\s+/i);
    if (titleMatch && titleMatch[1]) {
      const candidate = titleMatch[1].trim().replace(/\s+/g, ' ');
      if (candidate.length > 5 && candidate.length < 200 && /\d/.test(candidate)) {
        if (!/,\s*NY\b/i.test(candidate)) {
          return `${candidate}, New York, NY`;
        }
        return candidate;
      }
    }

    return null;
  }

  /**
   * Extract StreetEasy neighborhood name for context (not used for stats lookup).
   * @returns {string|null}
   */
  extractNeighborhood() {
    // Many StreetEasy page titles contain "... in <Neighborhood> : ..."
    const title = document.title || '';
    const match = title.match(/\bin\s+([^:|]+)\s*[:|]/i);
    if (match && match[1]) {
      const name = match[1].trim();
      if (name && name.length < 80) return name;
    }

    // Breadcrumb fallback
    const breadcrumb = document.querySelector('nav[aria-label="breadcrumb"], [aria-label="breadcrumb"]');
    if (breadcrumb) {
      const links = Array.from(breadcrumb.querySelectorAll('a'))
        .map(a => (a.textContent || '').trim())
        .filter(Boolean);
      // Often: ... > <Neighborhood> > <Building/Listing>
      if (links.length >= 2) {
        const candidate = links[links.length - 1];
        if (candidate && candidate.length < 80) return candidate;
      }
    }

    return null;
  }

  /**
   * Check if current page is a listing page
   * @returns {boolean}
   */
  static isListingPage() {
    const url = window.location.href;
    // StreetEasy listing URLs typically follow pattern:
    // /building/{id}, /rental/{id}, /sale/{id}
    return /\/(building|rental|sale)\//.test(url);
  }
}

// Make available globally
window.CoordinatesExtractor = CoordinatesExtractor;
