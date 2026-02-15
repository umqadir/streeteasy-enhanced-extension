/**
 * SleepEasy Area Analysis - Square Footage Estimation API
 *
 * Clean interface that abstracts the backend. Currently returns mock data.
 * When a real backend is ready, swap the implementation inside each method;
 * the return shape stays identical.
 */

class SqftEstimationAPI {
  constructor(options = {}) {
    this.baseUrl = options.baseUrl || null; // null = mock mode
    this.apiVersion = '0.1.0-mock';
  }

  /**
   * Single-photo scan: estimate visible floor area from one photo.
   * @param {string} imageUrl - Full URL of the listing photo
   * @returns {Promise<{estimatedSqft: number, confidence: number, apiVersion: string}>}
   */
  async estimateSinglePhoto(imageUrl) {
    if (this.baseUrl) {
      const res = await fetch(`${this.baseUrl}/estimate/single`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imageUrl }),
      });
      return res.json();
    }
    return this._mockSinglePhoto(imageUrl);
  }

  /**
   * Multi-photo room reconstruction: stitch multiple views of the same room.
   * @param {string[]} imageUrls - Array of photo URLs for the same room
   * @returns {Promise<{estimatedSqft: number, confidence: number, apiVersion: string, photosUsed: number}>}
   */
  async estimateMultiPhoto(imageUrls) {
    if (this.baseUrl) {
      const res = await fetch(`${this.baseUrl}/estimate/multi`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imageUrls }),
      });
      return res.json();
    }
    return this._mockMultiPhoto(imageUrls);
  }

  // ── Mock implementations ──

  /** @param {string} imageUrl */
  _mockSinglePhoto(imageUrl) {
    const hash = this._hashString(imageUrl);
    const sqft = 80 + (hash % 220);
    const confidence = 0.55 + (hash % 40) / 100;

    return new Promise(resolve => {
      setTimeout(() => {
        resolve({
          estimatedSqft: sqft,
          confidence: Math.round(confidence * 100) / 100,
          apiVersion: this.apiVersion,
        });
      }, 800 + (hash % 1200));
    });
  }

  /** @param {string[]} imageUrls */
  _mockMultiPhoto(imageUrls) {
    const combinedHash = imageUrls.reduce((h, url) => h ^ this._hashString(url), 0);
    const sqft = 120 + (Math.abs(combinedHash) % 380);
    const confidence = 0.65 + (Math.abs(combinedHash) % 30) / 100;

    return new Promise(resolve => {
      setTimeout(() => {
        resolve({
          estimatedSqft: sqft,
          confidence: Math.round(confidence * 100) / 100,
          apiVersion: this.apiVersion,
          photosUsed: imageUrls.length,
        });
      }, 1500 + (Math.abs(combinedHash) % 2000));
    });
  }

  /** Simple string hash for deterministic mock output. */
  _hashString(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }
}

if (typeof window !== 'undefined') {
  window.SqftEstimationAPI = SqftEstimationAPI;
}
