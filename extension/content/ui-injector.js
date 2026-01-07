/**
 * StreetSafe UI Injector
 * Injects the crime statistics module into StreetEasy pages
 */

class UIInjector {
  constructor() {
    this.shadowRoot = null;
    this.container = null;
    this.isInjected = false;
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

    // Find insertion point
    const anchor = this.findInsertionAnchor();
    if (!anchor) {
      console.warn('[StreetSafe] Could not find insertion anchor, using fallback');
      return false;
    }

    // Create container with Shadow DOM
    this.container = document.createElement('div');
    this.container.id = 'streetsafe-module';
    this.container.className = 'streetsafe-container';

    // Attach Shadow DOM for style encapsulation
    this.shadowRoot = this.container.attachShadow({ mode: 'open' });

    // Insert after anchor
    anchor.insertAdjacentElement('afterend', this.container);

    // Render content
    this.render(data);
    this.isInjected = true;

    console.log('[StreetSafe] UI injected successfully');
    return true;
  }

  /**
   * Find the best insertion point on the page
   * @returns {Element|null}
   */
  findInsertionAnchor() {
    // Try multiple potential anchors, in order of preference
    const selectors = [
      '[data-testid="listing-map"]',
      '.listing-map',
      '[class*="Map"]',
      '.building-details-summary',
      '[class*="Details"]',
      '[class*="Location"]',
      'h1'
    ];

    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element && element.offsetParent !== null) {
        // Element exists and is visible
        return element;
      }
    }

    return null;
  }

  /**
   * Render the module content
   * @param {Object} data - Crime statistics data
   */
  render(data) {
    if (!this.shadowRoot) return;

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

    const { geography, metrics, timeWindow, dataThrough, computedAt } = data;

    // Generate metric rows
    const metricRows = Object.entries(metrics).map(([key, metric]) => {
      const colorClass = getPercentileColorClass(metric.percentile);
      return `
        <div class="metric-row">
          <div class="metric-header">
            <span class="metric-name">${this.formatMetricName(key)}</span>
            <span class="metric-badge ${colorClass}">${formatPercentile(metric.percentile)}</span>
          </div>
          <div class="metric-stats">
            <div class="stat">
              <span class="stat-label">Count</span>
              <span class="stat-value">${formatNumber(metric.count)}</span>
            </div>
            <div class="stat">
              <span class="stat-label">Rate (per 100k)</span>
              <span class="stat-value">${formatRate(metric.rate)}</span>
            </div>
            <div class="stat">
              <span class="stat-label">Rank</span>
              <span class="stat-value">${metric.rank} of ${metric.total}</span>
            </div>
          </div>
        </div>
      `;
    }).join('');

    return `
      <div class="streetsafe-module">
        <div class="module-header">
          <h3>Crime & Safety <span class="data-source">(NYC Open Data)</span></h3>
          <button class="info-button" title="How this is computed">ℹ️</button>
        </div>

        <div class="time-window-selector">
          <label>Time period:</label>
          <select class="window-select">
            <option value="12m" ${timeWindow === '12m' ? 'selected' : ''}>Last 12 months</option>
            <option value="24m" ${timeWindow === '24m' ? 'selected' : ''}>Last 24 months</option>
            <option value="ytd" ${timeWindow === 'ytd' ? 'selected' : ''}>Calendar year</option>
          </select>
        </div>

        <div class="neighborhood-info">
          <strong>Neighborhood:</strong> ${geography.ntaName || geography.ntaId}
          <span class="borough">(${geography.borough})</span>
        </div>

        <div class="metrics-container">
          ${metricRows}
        </div>

        <div class="comparisons">
          <div class="comparison-row">
            <span>NYC Average (Murder):</span>
            <span>${formatRate(data.comparisons?.nycAverage?.murder || 0)} per 100k</span>
          </div>
          <div class="comparison-row">
            <span>Borough Average (Murder):</span>
            <span>${formatRate(data.comparisons?.boroughAverage?.murder || 0)} per 100k</span>
          </div>
        </div>

        <div class="module-footer">
          <div class="updated">Updated: ${new Date(dataThrough).toLocaleDateString()}</div>
          <a href="#" class="methodology-link">How this is computed</a>
          <button class="view-details-btn">View details</button>
        </div>

        <div class="limitations-note">
          <small>⚠️ Complaint data ≠ victimization risk. See methodology for limitations.</small>
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
        <div class="module-header">
          <h3>Crime & Safety <span class="data-source">(NYC Open Data)</span></h3>
        </div>
        <div class="loading-spinner">
          <div class="spinner"></div>
          <p>Loading crime statistics...</p>
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
        <div class="module-header">
          <h3>Crime & Safety <span class="data-source">(NYC Open Data)</span></h3>
        </div>
        <div class="error-message">
          <p>⚠️ Could not load crime statistics for this location.</p>
          <button class="view-details-btn">Open StreetSafe panel</button>
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
        background: white;
        border: 1px solid #e1e4e8;
        border-radius: 8px;
        padding: 20px;
        margin: 20px 0;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
      }

      .module-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 2px solid #f6f8fa;
      }

      .module-header h3 {
        font-size: 18px;
        font-weight: 600;
        color: #24292e;
        margin: 0;
      }

      .data-source {
        font-size: 12px;
        font-weight: 400;
        color: #6a737d;
      }

      .info-button {
        background: #f6f8fa;
        border: 1px solid #d1d5da;
        border-radius: 4px;
        padding: 4px 8px;
        cursor: pointer;
        font-size: 14px;
        transition: all 0.2s;
      }

      .info-button:hover {
        background: #e1e4e8;
      }

      .time-window-selector {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 16px;
        font-size: 14px;
      }

      .time-window-selector label {
        color: #586069;
        font-weight: 500;
      }

      .window-select {
        padding: 6px 12px;
        border: 1px solid #d1d5da;
        border-radius: 4px;
        background: white;
        font-size: 14px;
        cursor: pointer;
      }

      .neighborhood-info {
        background: #f6f8fa;
        padding: 12px;
        border-radius: 6px;
        margin-bottom: 16px;
        font-size: 14px;
        color: #24292e;
      }

      .borough {
        color: #6a737d;
        font-weight: 400;
      }

      .metrics-container {
        display: flex;
        flex-direction: column;
        gap: 16px;
        margin-bottom: 16px;
      }

      .metric-row {
        border: 1px solid #e1e4e8;
        border-radius: 6px;
        padding: 12px;
        background: #fafbfc;
      }

      .metric-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
      }

      .metric-name {
        font-weight: 600;
        font-size: 14px;
        color: #24292e;
      }

      .metric-badge {
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 500;
      }

      .percentile-high {
        background: #dcffe4;
        color: #0f5323;
      }

      .percentile-medium {
        background: #fff8c5;
        color: #735c0f;
      }

      .percentile-low {
        background: #ffe8cc;
        color: #8a4600;
      }

      .percentile-very-low {
        background: #ffdce0;
        color: #86181d;
      }

      .metric-stats {
        display: flex;
        gap: 16px;
      }

      .stat {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }

      .stat-label {
        font-size: 12px;
        color: #6a737d;
        font-weight: 500;
      }

      .stat-value {
        font-size: 16px;
        font-weight: 600;
        color: #24292e;
      }

      .comparisons {
        background: #f6f8fa;
        border-radius: 6px;
        padding: 12px;
        margin-bottom: 16px;
      }

      .comparison-row {
        display: flex;
        justify-content: space-between;
        padding: 6px 0;
        font-size: 13px;
        color: #586069;
      }

      .comparison-row span:last-child {
        font-weight: 600;
        color: #24292e;
      }

      .module-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-top: 12px;
        border-top: 1px solid #e1e4e8;
        font-size: 12px;
      }

      .updated {
        color: #6a737d;
      }

      .methodology-link {
        color: #0366d6;
        text-decoration: none;
        font-weight: 500;
      }

      .methodology-link:hover {
        text-decoration: underline;
      }

      .view-details-btn {
        background: #0366d6;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        transition: background 0.2s;
      }

      .view-details-btn:hover {
        background: #0256c7;
      }

      .limitations-note {
        margin-top: 12px;
        padding: 8px;
        background: #fff8c5;
        border-radius: 4px;
        font-size: 12px;
        color: #735c0f;
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
        border: 4px solid #f6f8fa;
        border-top-color: #0366d6;
        border-radius: 50%;
        animation: spin 1s linear infinite;
      }

      @keyframes spin {
        to { transform: rotate(360deg); }
      }

      .loading-spinner p {
        color: #6a737d;
        font-size: 14px;
      }

      /* Error state */
      .error-message {
        padding: 20px;
        text-align: center;
      }

      .error-message p {
        color: #d73a49;
        margin-bottom: 12px;
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
      violentCrime: 'Violent Crime Index'
    };
    return names[key] || key;
  }

  /**
   * Attach event listeners to interactive elements
   */
  attachEventListeners() {
    if (!this.shadowRoot) return;

    // Time window selector
    const windowSelect = this.shadowRoot.querySelector('.window-select');
    if (windowSelect) {
      windowSelect.addEventListener('change', (e) => {
        this.handleWindowChange(e.target.value);
      });
    }

    // Info button
    const infoButton = this.shadowRoot.querySelector('.info-button');
    if (infoButton) {
      infoButton.addEventListener('click', () => {
        this.openSidePanel();
      });
    }

    // Methodology link
    const methodologyLink = this.shadowRoot.querySelector('.methodology-link');
    if (methodologyLink) {
      methodologyLink.addEventListener('click', (e) => {
        e.preventDefault();
        this.openSidePanel();
      });
    }

    // View details button
    const viewDetailsBtn = this.shadowRoot.querySelector('.view-details-btn');
    if (viewDetailsBtn) {
      viewDetailsBtn.addEventListener('click', () => {
        this.openSidePanel();
      });
    }
  }

  /**
   * Handle time window change
   * @param {string} window - New time window
   */
  handleWindowChange(window) {
    console.log('[StreetSafe] Time window changed to:', window);
    // Send message to background script to refetch data
    chrome.runtime.sendMessage({
      type: 'CHANGE_TIME_WINDOW',
      window
    });
  }

  /**
   * Open the side panel
   */
  openSidePanel() {
    console.log('[StreetSafe] Opening side panel');
    chrome.runtime.sendMessage({
      type: 'OPEN_SIDE_PANEL'
    });
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
}

// Make available globally
window.UIInjector = UIInjector;
