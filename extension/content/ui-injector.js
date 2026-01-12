/**
 * SleepEasy UI Injector
 * Injects the crime statistics module into StreetEasy pages
 */

class UIInjector {
  constructor({ onMeasureChange } = {}) {
    this.shadowRoot = null;
    this.container = null;
    this.isInjected = false;
    this.currentData = null;
    this.onMeasureChange = typeof onMeasureChange === 'function' ? onMeasureChange : null;
  }

  /**
   * Inject the UI module into the page
   * @param {Object} data - Crime statistics data
   * @returns {boolean} Success status
   */
  inject(data) {
    if (this.isInjected) {
      this.update(data);
      return true;
    }

    const { container, anchor } = this.findInsertionPoint();

    // Create container with Shadow DOM
    this.container = document.createElement('div');
    this.container.id = 'streetsafe-module';
    this.container.className = 'streetsafe-container';

    // Attach Shadow DOM for style encapsulation
    this.shadowRoot = this.container.attachShadow({ mode: 'open' });

    // Insert in a stable, wide page container (avoid accidental insertion inside carousels/buttons).
    if (anchor) {
      anchor.insertAdjacentElement('afterend', this.container);
    } else if (container) {
      container.prepend(this.container);
    } else if (document.body) {
      document.body.prepend(this.container);
    } else {
      SleepEasyLog.warn('[SleepEasy] Could not inject UI (no container and no <body>)');
      return false;
    }

    // Render content
    this.render(data);
    this.isInjected = true;
    return true;
  }

  /**
   * Find a stable insertion container and optional anchor within it.
   * @returns {{container: Element|null, anchor: Element|null}}
   */
  findInsertionPoint() {
    const container = this.findInsertionContainer();
    if (!container) return { container: null, anchor: null };

    // Prefer anchors that are part of the main content flow and span most of the page width.
    const selectors = [
      // Listing pages: this block sits between the key details and fee disclosure.
      '[data-testid="buildingSummaryList"]',
      '[data-testid="propertyDetails"]',
      '[data-testid="listing-address"]',
      '[data-testid="listing-map"]',
      'h1',
      'h2',
      'h3'
    ];

    for (const selector of selectors) {
      const element = container.querySelector(selector) || document.querySelector(selector);
      if (this.isSafeInsertionAnchor(element)) return { container, anchor: element };
    }

    // If we didn't find a safe generic anchor, try to insert before common section headings.
    const headingTexts = [
      /about/i,
      /description/i,
      /facts/i,
      /amenities/i,
      /policies/i,
      /unit features/i,
      /available units/i
    ];

    const headings = Array.from(container.querySelectorAll('h2, h3'));
    for (const h of headings) {
      const text = (h.textContent || '').trim();
      if (!text) continue;
      if (!headingTexts.some(r => r.test(text))) continue;
      if (this.isSafeInsertionAnchor(h)) return { container, anchor: h };
    }

    return { container, anchor: null };
  }

  /**
   * Find a stable container element for insertion.
   * @returns {Element|null}
   */
  findInsertionContainer() {
    const candidates = [
      document.querySelector('main'),
      document.querySelector('[role="main"]'),
      document.querySelector('#site-content'),
      document.body
    ];

    for (const el of candidates) {
      if (!el) continue;
      if (el === document.body) return el;
      if (el.offsetParent !== null) return el;
    }

    return document.body || null;
  }

  /**
   * Ensure we don't insert inside an interactive element or tiny carousel cell.
   * @param {Element|null} element
   * @returns {boolean}
   */
  isSafeInsertionAnchor(element) {
    if (!element) return false;
    if (element.offsetParent === null) return false;
    if (element.closest('a, button, input, textarea, select, [role="button"], [role="link"]')) return false;

    const rect = element.getBoundingClientRect();
    // Most listing detail blocks are ~350–500px wide; avoid tiny carousel cells.
    const minWidth = Math.max(240, Math.floor(window.innerWidth * 0.25));
    if (rect.width < minWidth) return false;
    if (rect.height < 20) return false;

    return true;
  }

  /**
   * Render the module content
   * @param {Object} data - Crime statistics data
   */
  render(data) {
    if (!this.shadowRoot) return;

    this.currentData = data;

    const html = this.generateHTML(data);
    const css = this.generateCSS();

    this.shadowRoot.innerHTML = `
      <style>${css}</style>
      ${html}
    `;

    // Attach event listeners
    this.attachEventListeners();
  }

  /**
   * Update existing module with new data
   * @param {Object} data - Crime statistics data
   */
  update(data) {
    this.render(data);
  }

  /**
   * Generate HTML for the module
   * @param {Object} data - Crime statistics data
   * @returns {string}
   */
  generateHTML(data) {
    if (!data) {
      return this.generateLoadingHTML();
    }

    if (data.error) {
      return this.generateErrorHTML(data.error);
    }

    const { location, stats, uiState } = data;
    if (!stats) {
      return this.generateErrorHTML('Unexpected data format');
    }

    const { geography, metrics, timeWindow, measureDefs } = stats;
    const measure = uiState?.measure || 'ambient';

    const orderedMetricKeys = ['felonyAssault', 'propertyCrime', 'murder'];
    const metricRows = orderedMetricKeys
      .filter(k => metrics[k])
      .map((key) => {
        const metric = metrics[key];
        const m = metric?.measures?.[measure] || null;
        const def = measureDefs?.[measure] || null;

        const value = m?.value ?? null;
        const unit = def?.unit || '';
        const rank = m?.riskRank ?? null;
        const total = m?.total ?? null;

        const formattedValue = measure === 'count'
          ? formatNumber(value)
          : formatRate(value);

      return `
        <div class="metric">
          <div class="metric-name">${this.formatMetricName(key)}</div>
          <div class="metric-value">
            <span class="value">${this.escapeHtml(formattedValue)}</span>
            <span class="unit">${this.escapeHtml(unit)}</span>
          </div>
          ${rank && total ? `<div class="metric-rank">NYC risk rank ${rank}/${total}</div>` : ''}
        </div>
      `;
    })
    .join('');

    const measureLabel = this.getMeasureLabel(measure);
    const tooltip = this.getMeasureTooltip(measure);

    return `
      <div class="streetsafe-module">
        <div class="top">
          <div class="title">Crime</div>
          <div class="controls">
            <select class="measure-select" aria-label="Measure">
              <option value="ambient" ${measure === 'ambient' ? 'selected' : ''}>Ambient risk index</option>
              <option value="per100k" ${measure === 'per100k' ? 'selected' : ''}>Per 100k residents</option>
              <option value="perSqMi" ${measure === 'perSqMi' ? 'selected' : ''}>Per sq mi</option>
              <option value="count" ${measure === 'count' ? 'selected' : ''}>Raw incidents</option>
            </select>
            <span class="info" tabindex="0" aria-label="About ${this.escapeHtml(measureLabel)}" role="button">
              i
              <span class="tooltip" role="tooltip">${this.escapeHtml(tooltip)}</span>
            </span>
          </div>
        </div>

        <div class="metrics" aria-label="Crime metrics (${this.escapeHtml(measureLabel)})">
          ${metricRows}
        </div>
      </div>
      `;
  }

  /**
   * Generate loading state HTML
   * @returns {string}
   */
  generateLoadingHTML() {
    return `
      <div class="streetsafe-module loading">
        <div class="loading-spinner">
          <div class="spinner"></div>
          <p>Loading…</p>
        </div>
      </div>
    `;
  }

  /**
   * Generate error state HTML
   * @param {string} error - Error message
   * @returns {string}
   */
  generateErrorHTML(error) {
    return `
      <div class="streetsafe-module error">
        <div class="error-message">
          <p>Crime unavailable.</p>
        </div>
      </div>
    `;
  }

  /**
   * Generate CSS for the module
   * @returns {string}
   */
  generateCSS() {
    return `
      * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
      }

      .streetsafe-module {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        background: transparent;
        padding: 10px 0 6px 0;
      }

      .top {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 0 0 8px 0;
      }

      .title {
        font-size: 13px;
        font-weight: 800;
        color: #111827;
      }

      .controls {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
      }

      .measure-select {
        padding: 6px 8px;
        border: 1px solid rgba(27, 31, 36, 0.14);
        border-radius: 4px;
        background: white;
        font-size: 12px;
        cursor: pointer;
      }

      .info {
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        height: 18px;
        border-radius: 999px;
        border: 1px solid rgba(27, 31, 36, 0.14);
        background: white;
        color: #6b7280;
        font-size: 12px;
        font-weight: 800;
        line-height: 1;
        cursor: default;
        user-select: none;
      }

      .tooltip {
        position: absolute;
        top: calc(100% + 8px);
        right: 0;
        width: min(320px, 70vw);
        background: #111827;
        color: #f9fafb;
        padding: 10px 12px;
        border-radius: 8px;
        font-size: 12px;
        font-style: italic;
        font-weight: 500;
        line-height: 1.35;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.22);
        opacity: 0;
        transform: translateY(-4px);
        pointer-events: none;
        z-index: 9999;
        white-space: normal;
      }

      .info:hover .tooltip,
      .info:focus .tooltip,
      .info:focus-within .tooltip {
        opacity: 1;
        transform: translateY(0);
      }

      .metrics {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 12px;
        padding: 0;
      }

      .metric {
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
      }

      .metric-name {
        font-size: 12px;
        font-weight: 800;
        color: #111827;
      }

      .metric-value {
        display: inline-flex;
        gap: 6px;
        align-items: baseline;
      }

      .value {
        font-size: 13px;
        font-weight: 900;
        color: #111827;
      }

      .unit {
        font-size: 12px;
        font-weight: 600;
        color: #6b7280;
      }

      .metric-rank {
        font-size: 11px;
        font-weight: 600;
        color: #6b7280;
      }

      .dot {
        font-size: 12px;
        font-weight: 700;
        color: #9ca3af;
        padding: 0 2px;
      }

      @media (max-width: 520px) {
        .metrics { grid-template-columns: 1fr; gap: 10px; }
      }

      /* Loading state */
      .loading-spinner {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 40px 20px;
        gap: 12px;
      }

      .spinner {
        width: 40px;
        height: 40px;
        border: 4px solid rgba(17, 24, 39, 0.08);
        border-top-color: #2563eb;
        border-radius: 50%;
        animation: spin 1s linear infinite;
      }

      @keyframes spin {
        to { transform: rotate(360deg); }
      }

      .loading-spinner p {
        color: #6b7280;
        font-size: 14px;
      }

      /* Error state */
      .error-message {
        padding: 20px;
        text-align: center;
      }

      .error-message p {
        color: #6b7280;
        font-size: 13px;
        font-weight: 600;
      }
    `;
  }

  /**
   * Format metric names for display
   * @param {string} key - Metric key
   * @returns {string}
   */
  formatMetricName(key) {
    const names = {
      murder: 'Murder',
      felonyAssault: 'Felony Assault',
      propertyCrime: 'Property Crime'
    };
    return names[key] || key;
  }

  /**
   * Attach event listeners to interactive elements
   */
  attachEventListeners() {
    if (!this.shadowRoot) return;

    const measureSelect = this.shadowRoot.querySelector('.measure-select');
    if (measureSelect) {
      measureSelect.addEventListener('change', (e) => {
        this.handleMeasureChange(e.target.value);
      });
    }

  }

  /**
   * Handle measure change
   * @param {string} measure - New measure
   */
  handleMeasureChange(measure) {
    if (this.onMeasureChange) {
      this.onMeasureChange(measure);
    }
  }

  getMeasureLabel(measure) {
    const labels = {
      ambient: 'Ambient risk index',
      per100k: 'Per 100k residents',
      perSqMi: 'Per sq mi',
      count: 'Raw incidents'
    };
    return labels[measure] || 'Measure';
  }

  getMeasureTooltip(measure) {
    const base = 'Uses NYPD complaint counts mapped to the NYC NTA containing the listing (may differ from StreetEasy neighborhood labels). Last 24 months. NYC risk rank: 1 is highest risk.';
    const map = {
      ambient: `Ambient risk index: incidents per 100k average people present (residents + daytime workers). ${base}`,
      per100k: `Per 100k residents: incidents per 100k census residents. ${base}`,
      perSqMi: `Per sq mi: incidents per square mile of the NTA area. ${base}`,
      count: `Raw incidents: total incidents in the NTA. ${base}`
    };
    return map[measure] || base;
  }

  /**
   * Remove the module from the page
   */
  remove() {
    if (this.container && this.container.parentNode) {
      this.container.parentNode.removeChild(this.container);
      this.isInjected = false;
      this.shadowRoot = null;
      this.container = null;
    }
  }

  /**
   * Format source for display
   * @param {string} source - Source identifier
   * @returns {string}
   */
  formatSource(source) {
    const sources = {
      google_maps_link: 'Google Maps link',
      map_image: 'Map image URL',
      structured_data: 'Page structured data'
    };
    return sources[source] || source;
  }

  /**
   * Basic HTML escaping for safe inline rendering
   * @param {string} value
   * @returns {string}
   */
  escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

// Make available globally
window.UIInjector = UIInjector;
