/**
 * SleepEasy crime module.
 *
 * Renders neighborhood crime context inline on the listing page, inside a
 * Shadow DOM so StreetEasy's styles can't leak in (or ours out).
 *
 * Layout per metric:
 *   <name>                          <value> <unit>
 *   [gradient track with position dot]   #rank of N · x.x× NYC
 *
 * The dot sits at the neighborhood's risk percentile across all 197 NYC
 * NTAs for the selected measure (left = lower crime).
 */

class UIInjector {
  constructor({ onMeasureChange, onTimeWindowChange } = {}) {
    this.shadowRoot = null;
    this.container = null;
    this.isInjected = false;
    this.currentData = null;
    this.onMeasureChange = typeof onMeasureChange === 'function' ? onMeasureChange : null;
    this.onTimeWindowChange = typeof onTimeWindowChange === 'function' ? onTimeWindowChange : null;
  }

  /**
   * Inject the module into the page. Pass null data for the loading state.
   * @returns {boolean} success
   */
  inject(data) {
    if (this.isInjected) {
      this.update(data);
      return true;
    }

    const { container, anchor } = this.findInsertionPoint();

    this.container = document.createElement('div');
    this.container.id = 'sleepeasy-module';
    this.container.className = 'sleepeasy-container';
    this.shadowRoot = this.container.attachShadow({ mode: 'open' });

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

    this.render(data);
    this.isInjected = true;
    return true;
  }

  /** Reset internal state after the container was removed externally. */
  reset() {
    this.isInjected = false;
    this.container = null;
    this.shadowRoot = null;
  }

  /**
   * Find a stable insertion container and an anchor inside it.
   * @returns {{container: Element|null, anchor: Element|null}}
   */
  findInsertionPoint() {
    const container = this.findInsertionContainer();
    if (!container) return { container: null, anchor: null };

    // Prefer anchors in the main content flow that span most of the page width.
    const selectors = [
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

    // Fall back to inserting before common section headings.
    const headingTexts = [
      /about/i, /description/i, /facts/i, /amenities/i,
      /policies/i, /unit features/i, /available units/i
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

  /** Don't insert inside interactive elements or tiny carousel cells. */
  isSafeInsertionAnchor(element) {
    if (!element) return false;
    if (element.offsetParent === null) return false;
    if (element.closest('a, button, input, textarea, select, [role="button"], [role="link"]')) return false;

    const rect = element.getBoundingClientRect();
    const minWidth = Math.max(240, Math.floor(window.innerWidth * 0.25));
    if (rect.width < minWidth) return false;
    if (rect.height < 20) return false;

    return true;
  }

  render(data) {
    if (!this.shadowRoot) return;

    this.currentData = data;
    this.shadowRoot.innerHTML = `
      <style>${this.generateCSS()}</style>
      ${this.generateHTML(data)}
    `;
    this.attachEventListeners();
  }

  update(data) {
    this.render(data);
  }

  generateHTML(data) {
    if (!data) return this.generateLoadingHTML();
    if (data.error) return this.generateErrorHTML(data.error);

    const { stats, uiState } = data;
    if (!stats) return this.generateErrorHTML('Unexpected data format');

    const { geography, metrics, city, measureDefs, timeWindowDefs, dataThrough } = stats;
    const measure = uiState?.measure || 'ambient';
    const timeWindow = uiState?.timeWindow || stats.timeWindow || '24m';

    const orderedMetricKeys = ['felonyAssault', 'propertyCrime', 'murder'];
    const metricRows = orderedMetricKeys
      .filter(k => metrics[k])
      .map(key => this.generateMetricRow(key, metrics[key], city?.[key], measure, measureDefs))
      .join('');

    const measureOptions = Object.values(measureDefs || {})
      .map(def => `<option value="${this.escapeHtml(def.id)}" ${def.id === measure ? 'selected' : ''}>${this.escapeHtml(def.label)}</option>`)
      .join('');

    const windowOptions = Object.values(timeWindowDefs || {})
      .map(def => `<option value="${this.escapeHtml(def.id)}" ${def.id === timeWindow ? 'selected' : ''}>${this.escapeHtml(def.label)}</option>`)
      .join('');

    const place = geography?.ntaName
      ? `${geography.ntaName}${geography.borough ? `, ${geography.borough}` : ''}`
      : 'This neighborhood';

    const tooltip = this.getTooltip(measure, timeWindow, dataThrough);

    return `
      <section class="se-module" aria-label="Neighborhood crime statistics">
        <div class="se-head">
          <div class="se-titles">
            <div class="se-label">Crime</div>
            <h3 class="se-place">${this.escapeHtml(place)}</h3>
          </div>
          <span class="se-info" tabindex="0" role="button" aria-label="About these statistics">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="7.3" y="6.8" width="1.4" height="4.6" rx="0.7" fill="currentColor"/><circle cx="8" cy="4.7" r="0.9" fill="currentColor"/></svg>
            <span class="se-tooltip" role="tooltip">${tooltip}</span>
          </span>
        </div>

        <div class="se-scale" aria-hidden="true">
          <span>lower crime</span>
          <span>higher crime</span>
        </div>

        <div class="se-metrics">
          ${metricRows}
        </div>

        <div class="se-controls">
          <select class="se-select se-measure-select" aria-label="Crime measure">
            ${measureOptions}
          </select>
          <select class="se-select se-window-select" aria-label="Time window">
            ${windowOptions}
          </select>
        </div>

        <div class="se-foot">NYPD complaints via NYC Open Data · through ${this.escapeHtml(this.formatDate(dataThrough))}</div>
      </section>
    `;
  }

  generateMetricRow(key, metric, cityValues, measure, measureDefs) {
    const m = metric?.measures?.[measure] || null;
    const def = measureDefs?.[measure] || null;

    const value = m?.value ?? null;
    const unit = def?.unit || '';
    const rank = m?.riskRank ?? null;
    const total = m?.total ?? null;

    const formattedValue = measure === 'count' ? formatNumber(value) : formatRate(value);
    const multiple = formatCityMultiple(value, cityValues?.[measure]);

    // Dot position from rank percentile (rank 1 = lowest risk = far left)
    let dotHtml = '';
    let pct = null;
    if (rank && total && total > 1) {
      pct = ((rank - 1) / (total - 1)) * 100;
      dotHtml = `<span class="se-dot" style="left: ${pct.toFixed(1)}%"></span>`;
    }

    const rankBits = [];
    if (rank && total) rankBits.push(`#${rank} of ${total}`);
    if (multiple) rankBits.push(`${multiple} NYC rate`);

    return `
      <div class="se-metric">
        <div class="se-metric-line">
          <span class="se-metric-name">${this.formatMetricName(key)}</span>
          <span class="se-metric-value">
            <strong>${this.escapeHtml(formattedValue)}</strong>
            <span class="se-unit">${this.escapeHtml(unit)}</span>
          </span>
        </div>
        <div class="se-metric-line se-metric-sub">
          <span class="se-track${value === null ? ' se-track-empty' : ''}" aria-hidden="true">${dotHtml}</span>
          <span class="se-rank">${this.escapeHtml(rankBits.join(' · ') || '—')}</span>
        </div>
      </div>
    `;
  }

  generateLoadingHTML() {
    return `
      <section class="se-module" aria-label="Neighborhood crime statistics (loading)">
        <div class="se-head">
          <div class="se-titles">
            <div class="se-label">Crime</div>
            <div class="se-skeleton se-skeleton-title"></div>
          </div>
        </div>
        <div class="se-metrics">
          <div class="se-skeleton se-skeleton-row"></div>
          <div class="se-skeleton se-skeleton-row"></div>
          <div class="se-skeleton se-skeleton-row"></div>
        </div>
      </section>
    `;
  }

  generateErrorHTML() {
    return `
      <section class="se-module se-error-state">
        <div class="se-head">
          <div class="se-titles">
            <div class="se-label">Crime</div>
          </div>
        </div>
        <p class="se-error">Crime data unavailable for this listing.</p>
      </section>
    `;
  }

  generateCSS() {
    return `
      :host { all: initial; }
      * { box-sizing: border-box; margin: 0; padding: 0; }

      .se-module {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        color: #111827;
        max-width: 640px;
        padding: 14px 0 10px;
        border-top: 1px solid #e5e7eb;
        border-bottom: 1px solid #e5e7eb;
        margin: 12px 0;
      }

      .se-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 8px;
      }

      .se-label {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #6b7280;
      }

      .se-place {
        font-size: 15px;
        font-weight: 800;
        letter-spacing: -0.01em;
        color: #111827;
        margin-top: 1px;
      }

      .se-info {
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 22px;
        height: 22px;
        border-radius: 999px;
        color: #9ca3af;
        cursor: default;
        user-select: none;
        flex: 0 0 auto;
      }

      .se-info:hover, .se-info:focus { color: #4b5563; outline: none; }

      .se-tooltip {
        position: absolute;
        top: calc(100% + 8px);
        right: 0;
        width: min(340px, 78vw);
        background: #111827;
        color: #f9fafb;
        padding: 10px 12px;
        border-radius: 8px;
        font-size: 12px;
        font-weight: 500;
        line-height: 1.45;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.22);
        opacity: 0;
        transform: translateY(-4px);
        pointer-events: none;
        transition: opacity 0.12s ease, transform 0.12s ease;
        z-index: 9999;
        white-space: normal;
      }

      .se-info:hover .se-tooltip,
      .se-info:focus .se-tooltip,
      .se-info:focus-within .se-tooltip {
        opacity: 1;
        transform: translateY(0);
      }

      .se-scale {
        display: flex;
        justify-content: space-between;
        font-size: 10px;
        font-weight: 600;
        color: #9ca3af;
        letter-spacing: 0.02em;
        margin-bottom: 6px;
      }

      .se-metrics {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }

      .se-metric-line {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
      }

      .se-metric-name {
        font-size: 13px;
        font-weight: 700;
        color: #111827;
      }

      .se-metric-value strong {
        font-size: 14px;
        font-weight: 800;
        font-variant-numeric: tabular-nums;
        letter-spacing: -0.01em;
      }

      .se-unit {
        font-size: 11px;
        font-weight: 600;
        color: #6b7280;
        margin-left: 2px;
      }

      .se-metric-sub {
        align-items: center;
        margin-top: 5px;
      }

      .se-track {
        position: relative;
        flex: 1 1 auto;
        height: 3.5px;
        border-radius: 999px;
        background: linear-gradient(90deg, #79b292 0%, #ddc287 55%, #d99087 100%);
      }

      .se-track-empty {
        background: #e5e7eb;
        opacity: 1;
      }

      .se-dot {
        position: absolute;
        top: 50%;
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: #fff;
        border: 2px solid #1f2937;
        transform: translate(-50%, -50%);
        box-shadow: 0 1px 2px rgba(0,0,0,0.2);
      }

      .se-rank {
        flex: 0 0 auto;
        font-size: 11px;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: #6b7280;
        white-space: nowrap;
      }

      .se-controls {
        display: flex;
        gap: 8px;
        margin-top: 14px;
      }

      .se-select {
        appearance: none;
        -webkit-appearance: none;
        padding: 5px 24px 5px 9px;
        border: 1px solid #d1d5db;
        border-radius: 6px;
        background: #fff url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%236b7280' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat right 8px center;
        font-family: inherit;
        font-size: 12px;
        font-weight: 600;
        color: #374151;
        cursor: pointer;
      }

      .se-select:hover { border-color: #9ca3af; }
      .se-select:focus { outline: none; border-color: #111827; }

      .se-foot {
        margin-top: 10px;
        font-size: 11px;
        font-weight: 500;
        color: #9ca3af;
      }

      .se-error {
        font-size: 13px;
        font-weight: 600;
        color: #6b7280;
        padding: 4px 0 8px;
      }

      /* Loading skeletons */
      .se-skeleton {
        background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
        background-size: 200% 100%;
        animation: se-shimmer 1.2s ease-in-out infinite;
        border-radius: 4px;
      }
      .se-skeleton-title { width: 180px; height: 16px; margin-top: 4px; }
      .se-skeleton-row { width: 100%; height: 30px; }

      @keyframes se-shimmer {
        0% { background-position: 200% 0; }
        100% { background-position: -200% 0; }
      }

      @media (max-width: 520px) {
        .se-rank { font-size: 10px; }
        .se-controls { flex-wrap: wrap; }
      }
    `;
  }

  formatMetricName(key) {
    const names = {
      murder: 'Murder',
      felonyAssault: 'Felony assault',
      propertyCrime: 'Property crime'
    };
    return names[key] || key;
  }

  formatDate(isoDate) {
    if (!isoDate) return 'latest available';
    const [y, m, d] = String(isoDate).split('-').map(Number);
    if (!y || !m || !d) return String(isoDate);
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${months[m - 1]} ${d}, ${y}`;
  }

  attachEventListeners() {
    if (!this.shadowRoot) return;

    const measureSelect = this.shadowRoot.querySelector('.se-measure-select');
    if (measureSelect && this.onMeasureChange) {
      measureSelect.addEventListener('change', (e) => this.onMeasureChange(e.target.value));
    }

    const windowSelect = this.shadowRoot.querySelector('.se-window-select');
    if (windowSelect && this.onTimeWindowChange) {
      windowSelect.addEventListener('change', (e) => this.onTimeWindowChange(e.target.value));
    }
  }

  getTooltip(measure, timeWindow, dataThrough) {
    const measureLines = {
      ambient: 'Ambient risk index: incidents per 100,000 people actually present — residents (16h/day) plus daytime workers (8h/day) — so business districts aren’t distorted.',
      per100k: 'Incidents per 100,000 census residents (2020).',
      perSqMi: 'Incidents per square mile of neighborhood area.',
      count: 'Raw incident counts for the neighborhood.'
    };
    const windowLabels = { '3m': 'last 3 months', '12m': 'last 12 months', '24m': 'last 24 months' };

    const parts = [
      measureLines[measure] || '',
      `NYPD complaint data (${windowLabels[timeWindow] || timeWindow}, through ${this.formatDate(dataThrough)}) mapped to the official NYC neighborhood (NTA) containing this listing — which may differ from StreetEasy’s neighborhood label.`,
      'The dot shows where this neighborhood ranks across all 197 NYC neighborhoods: #1 = lowest rate. Past incidence does not predict future safety.'
    ];
    return parts.filter(Boolean).map(p => this.escapeHtml(p)).join('<br><br>');
  }

  remove() {
    if (this.container && this.container.parentNode) {
      this.container.parentNode.removeChild(this.container);
    }
    this.reset();
  }

  escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

window.UIInjector = UIInjector;
