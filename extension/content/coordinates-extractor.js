/**
 * StreetSafe Coordinates Extractor
 * Extracts listing location from StreetEasy pages
 */

class CoordinatesExtractor {
  constructor() {
    this.coordinates = null;
    this.address = null;
  }

  /**
   * Extract coordinates from the page
   * @returns {Promise<Object|null>} {lat, lon, source, address}
   */
  async extract() {
    console.log('[StreetSafe] Starting coordinate extraction...');

    // Method 1: Parse static map image URL (most reliable)
    const mapCoords = this.extractFromMapImage();
    if (mapCoords) {
      console.log('[StreetSafe] Extracted from map image:', mapCoords);
      this.coordinates = mapCoords;
      this.address = this.extractAddress();
      return {
        ...mapCoords,
        source: 'map_image',
        address: this.address
      };
    }

    // Method 2: Look for structured data (JSON-LD, meta tags, etc.)
    const structuredCoords = this.extractFromStructuredData();
    if (structuredCoords) {
      console.log('[StreetSafe] Extracted from structured data:', structuredCoords);
      this.coordinates = structuredCoords;
      this.address = this.extractAddress();
      return {
        ...structuredCoords,
        source: 'structured_data',
        address: this.address
      };
    }

    // Method 3: Fallback to geocoding the address
    this.address = this.extractAddress();
    if (this.address) {
      console.log('[StreetSafe] Attempting geocoding for:', this.address);
      const geocodedCoords = await geocodeAddress(this.address);
      if (geocodedCoords) {
        console.log('[StreetSafe] Geocoded successfully:', geocodedCoords);
        this.coordinates = { lat: geocodedCoords.lat, lon: geocodedCoords.lon };
        return {
          lat: geocodedCoords.lat,
          lon: geocodedCoords.lon,
          source: 'geocoded',
          address: geocodedCoords.label || this.address
        };
      }
    }

    console.warn('[StreetSafe] Could not extract coordinates');
    return null;
  }

  /**
   * Extract coordinates from static map image URL
   * @returns {Object|null} {lat, lon}
   */
  extractFromMapImage() {
    // Look for Google Static Maps images
    const images = document.querySelectorAll('img[src*="maps.googleapis.com"], img[src*="staticmap"]');

    for (const img of images) {
      const coords = extractCoordinatesFromMapUrl(img.src);
      if (coords) {
        return coords;
      }
    }

    // Also check background images in style attributes
    const elementsWithBg = document.querySelectorAll('[style*="background"]');
    for (const el of elementsWithBg) {
      const style = el.getAttribute('style');
      if (style && (style.includes('maps.googleapis.com') || style.includes('staticmap'))) {
        const urlMatch = style.match(/url\(['"]?([^'"()]+)['"]?\)/);
        if (urlMatch) {
          const coords = extractCoordinatesFromMapUrl(urlMatch[1]);
          if (coords) {
            return coords;
          }
        }
      }
    }

    return null;
  }

  /**
   * Extract coordinates from structured data (JSON-LD, meta tags, etc.)
   * @returns {Object|null} {lat, lon}
   */
  extractFromStructuredData() {
    // Look for JSON-LD structured data
    const jsonLdScripts = document.querySelectorAll('script[type="application/ld+json"]');

    for (const script of jsonLdScripts) {
      try {
        const data = JSON.parse(script.textContent);

        // Check for geo coordinates in various structured data formats
        if (data.geo) {
          if (data.geo.latitude && data.geo.longitude) {
            return {
              lat: parseFloat(data.geo.latitude),
              lon: parseFloat(data.geo.longitude)
            };
          }
        }

        // Check for address with geo
        if (data.address && data.address.geo) {
          if (data.address.geo.latitude && data.address.geo.longitude) {
            return {
              lat: parseFloat(data.address.geo.latitude),
              lon: parseFloat(data.address.geo.longitude)
            };
          }
        }
      } catch (e) {
        // Invalid JSON, skip
        continue;
      }
    }

    // Look for meta tags
    const latMeta = document.querySelector('meta[property="place:location:latitude"], meta[name="geo.position"]');
    const lonMeta = document.querySelector('meta[property="place:location:longitude"]');

    if (latMeta && lonMeta) {
      const lat = parseFloat(latMeta.getAttribute('content'));
      const lon = parseFloat(lonMeta.getAttribute('content'));
      if (!isNaN(lat) && !isNaN(lon)) {
        return { lat, lon };
      }
    }

    // Check for geo.position meta tag (format: "lat;lon")
    if (latMeta) {
      const content = latMeta.getAttribute('content');
      if (content && content.includes(';')) {
        const [lat, lon] = content.split(';').map(parseFloat);
        if (!isNaN(lat) && !isNaN(lon)) {
          return { lat, lon };
        }
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
          // Add "New York, NY" if not present
          if (!text.toLowerCase().includes('new york') && !text.toLowerCase().includes('ny')) {
            text += ', New York, NY';
          }
          return text;
        }
      }
    }

    // Try to extract from URL if it contains neighborhood info
    const urlMatch = window.location.pathname.match(/\/([^/]+)\/[^/]+$/);
    if (urlMatch) {
      const neighborhood = urlMatch[1].replace(/-/g, ' ');
      return `${neighborhood}, New York, NY`;
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
