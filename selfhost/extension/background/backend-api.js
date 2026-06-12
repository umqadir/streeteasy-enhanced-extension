/**
 * Client for the optional local square-footage backend.
 *
 * The backend is a Python HTTP server the user runs on their own machine
 * (default http://127.0.0.1:8787). The extension never contacts any other
 * host for photo analysis.
 *
 * Endpoints: GET /health, GET|POST /backend/config,
 *            POST /estimate/single, POST /estimate/multi
 */

const BACKEND_CONFIG_KEY = 'area:backend:config';

export const DEFAULT_BACKEND_CONFIG = Object.freeze({
  baseUrl: 'http://127.0.0.1:8787',
  devicePolicy: 'auto',     // auto | cpu | mps
  analysisMode: 'auto',     // auto (DUSt3R multi-view when CUDA available) | single-image
  noCudaPromptHandled: false,
});

export class SqftEstimationAPI {
  constructor(options = {}) {
    this.apiVersion = 'v2-local-client';
    this.config = this._sanitizeConfig({ ...DEFAULT_BACKEND_CONFIG, ...options });
    this._ready = this._loadConfig();
  }

  async _loadConfig() {
    try {
      const result = await chrome.storage.local.get(BACKEND_CONFIG_KEY);
      const stored = result?.[BACKEND_CONFIG_KEY] || {};
      this.config = this._sanitizeConfig({ ...this.config, ...stored });
    } catch {
      this.config = this._sanitizeConfig(this.config);
    }
  }

  _sanitizeConfig(config = {}) {
    let baseUrl = String(config.baseUrl || DEFAULT_BACKEND_CONFIG.baseUrl).trim().replace(/\/+$/, '');
    try {
      const u = new URL(baseUrl);
      if (!['http:', 'https:'].includes(u.protocol)) baseUrl = DEFAULT_BACKEND_CONFIG.baseUrl;
    } catch {
      baseUrl = DEFAULT_BACKEND_CONFIG.baseUrl;
    }
    const devicePolicy = ['auto', 'cpu', 'mps'].includes(String(config.devicePolicy))
      ? String(config.devicePolicy)
      : 'auto';
    const analysisMode = ['auto', 'single-image'].includes(String(config.analysisMode))
      ? String(config.analysisMode)
      : 'auto';
    return {
      baseUrl,
      devicePolicy,
      analysisMode,
      noCudaPromptHandled: config.noCudaPromptHandled === true,
    };
  }

  async ready() {
    await this._ready;
  }

  async getConfig() {
    await this.ready();
    return { ...this.config };
  }

  async setConfig(nextConfig = {}) {
    await this.ready();
    this.config = this._sanitizeConfig({ ...this.config, ...nextConfig });
    await chrome.storage.local.set({ [BACKEND_CONFIG_KEY]: this.config });
    await this._pushBackendConfig();
    return { ...this.config };
  }

  async _pushBackendConfig() {
    await this._fetchJson('/backend/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        devicePolicy: this.config.devicePolicy,
        analysisMode: this.config.analysisMode,
      }),
    }, 15000);
  }

  async _fetchJson(path, options = {}, timeoutMs = 300000) {
    const baseUrl = this.config.baseUrl;
    if (!baseUrl) throw new Error('Backend URL is empty');

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(`${baseUrl}${path}`, { ...options, signal: controller.signal });
      if (!res.ok) {
        let detail = '';
        try {
          const body = await res.json();
          detail = body?.error || body?.message || '';
        } catch {
          detail = await res.text().catch(() => '');
        }
        throw new Error(`Backend ${res.status}${detail ? `: ${detail}` : ''}`);
      }
      return await res.json();
    } catch (err) {
      if (err?.name === 'AbortError') {
        throw new Error(`Backend request timed out after ${Math.round(timeoutMs / 1000)}s (${path})`);
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  async checkHealth() {
    await this.ready();
    try {
      return await this._fetchJson('/health', {}, 10000);
    } catch (err) {
      return { ok: false, error: err?.message || 'Backend unavailable' };
    }
  }

  async estimateSinglePhoto(imageUrl) {
    await this.ready();
    await this._pushBackendConfig();
    return await this._fetchJson('/estimate/single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        imageUrl,
        devicePolicy: this.config.devicePolicy,
      }),
    });
  }

  async estimateMultiPhoto(imageUrls, options = {}) {
    await this.ready();
    const requested = String(options.multiviewMethod || '').trim().toLowerCase();
    const effectiveMethod = (imageUrls.length <= 1)
      ? 'single-image'
      : (requested || (this.config.analysisMode === 'single-image' ? 'single-image' : 'dust3r-scene'));
    if (!['dust3r-scene', 'single-image'].includes(effectiveMethod)) {
      throw new Error(`Unsupported multiview method: ${effectiveMethod}`);
    }
    await this._pushBackendConfig();
    return await this._fetchJson('/estimate/multi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        imageUrls,
        devicePolicy: this.config.devicePolicy,
        multiviewMethod: effectiveMethod,
      }),
    });
  }
}
