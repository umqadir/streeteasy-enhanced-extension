/**
 * SleepEasy Side Panel - Sq Ft Analysis (Room-Centric)
 *
 * A single accordion list of listings, ordered by most recently modified.
 * Each listing is collapsible; expanding lazily loads rooms and renders the
 * room-centric controls (estimate, rename, remove photo, delete room).
 */

(function () {
  'use strict';

  // ── State ──

  /** @type {Array<{id: string, url: string, address: (string|null), updatedAt: number, roomCount: (number|null), totalSqft: (number|null)}>} */
  let listingSummaries = [];

  /** @type {Map<string, {loading: boolean, error: (string|null), listing: any, rooms: any[], positions: Object<string, number>}>} */
  const listingState = new Map();

  /** @type {Set<string>} */
  const openListingIds = new Set();

  /** @type {Set<string>} */
  const analyzingRoomIds = new Set();

  /** @type {string|null} */
  let activeListingId = null;

  let historyRefreshTimer = null;

  // ── DOM refs ──

  const listingsEl = document.getElementById('listings');
  const settingsToggle = document.getElementById('settings-toggle');
  const settingsPanel = document.getElementById('settings-panel');
  const backendUrlEl = document.getElementById('backend-url');
  const backendDeviceEl = document.getElementById('backend-device');
  const backendAnalysisModeEl = document.getElementById('backend-analysis-mode');
  const backendSaveEl = document.getElementById('backend-save');
  const backendCheckEl = document.getElementById('backend-check');
  const backendStatusEl = document.getElementById('backend-status');

  // ── Init ──

  async function initialize() {
    attachDelegatedListeners();
    initSettingsToggle();
    listenForUpdates();
    initBackendControls();
    await Promise.all([
      refreshAll({ initial: true }),
      loadBackendConfig(),
    ]);
  }

  // ── Settings toggle ──

  function initSettingsToggle() {
    if (!settingsToggle || !settingsPanel) return;
    settingsToggle.addEventListener('click', () => {
      const isHidden = settingsPanel.classList.toggle('hidden');
      settingsToggle.classList.toggle('active', !isHidden);
    });
  }

  // ── Data loading ──

  async function getActiveTabListingContext() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return null;
    try {
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: 'GET_LISTING_CONTEXT',
        target: 'content',
      });
      if (!response?.listingId) return null;
      return response;
    } catch {
      return null;
    }
  }

  async function loadHistorySummaries() {
    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_HISTORY' });
      if (!res?.success) return [];
      return res.history || [];
    } catch {
      return [];
    }
  }

  async function refreshAll({ initial = false } = {}) {
    const ctx = await getActiveTabListingContext();
    activeListingId = ctx?.listingId || null;

    const history = await loadHistorySummaries();
    listingSummaries = history;

    if (activeListingId && !listingSummaries.some(l => l.id === activeListingId)) {
      listingSummaries = [{
        id: activeListingId,
        url: ctx?.listingUrl || '',
        address: ctx?.address || activeListingId,
        updatedAt: Date.now(),
        roomCount: null,
        totalSqft: null,
      }, ...listingSummaries];
    }

    if (initial) {
      const defaultOpen = activeListingId || listingSummaries[0]?.id || null;
      if (defaultOpen) openListingIds.add(defaultOpen);
    }

    render();

    for (const id of openListingIds) {
      void ensureListingStateLoaded(id);
    }
  }

  // ── Backend settings ──

  function initBackendControls() {
    if (!backendSaveEl || !backendCheckEl) return;
    backendSaveEl.addEventListener('click', () => void saveBackendConfig());
    backendCheckEl.addEventListener('click', () => void checkBackendHealth());
  }

  function setBackendStatus(message, level = 'info') {
    if (!backendStatusEl) return;
    backendStatusEl.textContent = message || '';
    backendStatusEl.classList.remove('ok', 'warn', 'error');
    if (['ok', 'warn', 'error'].includes(level)) {
      backendStatusEl.classList.add(level);
    }
  }

  function applyBackendConfigToForm(config) {
    if (!config) return;
    if (backendUrlEl) backendUrlEl.value = config.baseUrl || 'http://127.0.0.1:8787';
    if (backendDeviceEl) backendDeviceEl.value = config.devicePolicy || 'auto';
    if (backendAnalysisModeEl) backendAnalysisModeEl.value = config.analysisMode || 'auto';
  }

  function readBackendConfigFromForm() {
    return {
      mode: 'local',
      baseUrl: (backendUrlEl?.value || '').trim(),
      devicePolicy: backendDeviceEl?.value || 'auto',
      analysisMode: backendAnalysisModeEl?.value || 'auto',
    };
  }

  async function loadBackendConfig() {
    if (!backendUrlEl) return;
    setBackendStatus('Loading...');
    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_BACKEND_CONFIG' });
      if (!res?.success) {
        setBackendStatus(`Failed: ${res?.error || 'unknown'}`, 'error');
        return;
      }
      applyBackendConfigToForm(res.config || {});
      renderHealthStatus(res.health);
    } catch (err) {
      setBackendStatus(`Failed: ${err?.message || 'unknown'}`, 'error');
    }
  }

  function renderHealthStatus(health) {
    if (!health) { setBackendStatus('No status yet', 'warn'); return; }
    if (health.ok) {
      const device = health?.device?.policy ? ` (${health.device.policy})` : '';
      const cuda = Boolean(health?.capabilities?.cudaAvailable);
      const analysis = health?.analysisMode ? ` · ${health.analysisMode}` : '';
      setBackendStatus(`Connected${device} · ${cuda ? 'CUDA' : 'No CUDA'}${analysis}`, cuda ? 'ok' : 'warn');
      return;
    }
    setBackendStatus(`Unavailable: ${health.error || 'unknown'}`, 'error');
  }

  async function updateBackendConfig(partialConfig) {
    const res = await chrome.runtime.sendMessage({ type: 'SET_BACKEND_CONFIG', config: partialConfig });
    if (!res?.success) {
      throw new Error(res?.error || 'Failed to save backend config');
    }
    applyBackendConfigToForm(res.config || partialConfig);
    renderHealthStatus(res.health);
    return res;
  }

  async function saveBackendConfig() {
    setBackendStatus('Saving...');
    try {
      const config = readBackendConfigFromForm();
      await updateBackendConfig(config);
    } catch (err) {
      setBackendStatus(`Failed: ${err?.message || 'unknown'}`, 'error');
    }
  }

  async function checkBackendHealth() {
    setBackendStatus('Checking...');
    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_BACKEND_HEALTH' });
      if (!res?.success) { setBackendStatus(`Failed: ${res?.error || 'unknown'}`, 'error'); return; }
      renderHealthStatus(res.health);
    } catch (err) {
      setBackendStatus(`Failed: ${err?.message || 'unknown'}`, 'error');
    }
  }

  async function ensureListingStateLoaded(listingId) {
    if (!listingId) return;
    const existing = listingState.get(listingId);
    if (existing && (existing.loading || existing.rooms)) return;

    listingState.set(listingId, { loading: true, error: null, listing: null, rooms: [], positions: {} });
    render();

    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_LISTING_STATE', listingId });
      if (!res?.success) {
        listingState.set(listingId, { loading: false, error: res?.error || 'Failed to load', listing: null, rooms: [], positions: {} });
        render();
        return;
      }
      listingState.set(listingId, { loading: false, error: null, listing: res.listing || null, rooms: res.rooms || [], positions: res.positions || {} });
      render();
    } catch (err) {
      listingState.set(listingId, { loading: false, error: err?.message || 'Failed to load', listing: null, rooms: [], positions: {} });
      render();
    }
  }

  // ── Live updates ──

  function listenForUpdates() {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.target !== 'sidepanel') return;
      if (msg.type !== 'STATE_CHANGED' || !msg.listingId) return;

      const prev = listingState.get(msg.listingId) || { loading: false, error: null, listing: null, rooms: [], positions: {} };
      listingState.set(msg.listingId, {
        ...prev,
        loading: false,
        error: null,
        rooms: msg.rooms || [],
        positions: msg.positions || prev.positions || {},
      });

      scheduleHistoryRefresh();
      render();
    });

    chrome.tabs.onActivated?.addListener(() => refreshAll());
  }

  function scheduleHistoryRefresh() {
    if (historyRefreshTimer) clearTimeout(historyRefreshTimer);
    historyRefreshTimer = setTimeout(async () => {
      historyRefreshTimer = null;
      const history = await loadHistorySummaries();
      listingSummaries = history;
      if (activeListingId && !listingSummaries.some(l => l.id === activeListingId)) {
        listingSummaries = [{ id: activeListingId, url: '', address: activeListingId, updatedAt: Date.now(), roomCount: null, totalSqft: null }, ...listingSummaries];
      }
      render();
    }, 350);
  }

  // ── Rendering ──

  const CHEVRON_SVG = '<svg class="listing-chevron" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function render() {
    if (!listingSummaries.length) {
      listingsEl.innerHTML = `
        <div class="empty-state">
          <p>No listings yet</p>
          <span class="hint">Open a StreetEasy listing and hover photos to assign rooms</span>
        </div>
      `;
      return;
    }

    listingsEl.innerHTML = listingSummaries.map(summary => renderListingSection(summary)).join('');

    listingsEl.querySelectorAll('details.listing').forEach(details => {
      const id = details.dataset.listingId;
      if (!id) return;
      details.open = openListingIds.has(id);
    });
  }

  function renderListingSection(summary) {
    const id = summary.id;
    const st = listingState.get(id) || null;
    const address = escapeHtml(summary.address || id);
    const date = summary.updatedAt ? new Date(summary.updatedAt).toLocaleDateString() : '';

    const subtitleBits = [];
    if (summary.roomCount != null) subtitleBits.push(summary.roomCount === 1 ? '1 room' : `${summary.roomCount} rooms`);
    if (summary.totalSqft != null) subtitleBits.push(`${summary.totalSqft} sqft`);
    if (date) subtitleBits.push(date);

    let bodyHtml = '';
    if (st?.loading) {
      bodyHtml = `<div class="listing-body"><div class="empty-state compact"><span class="hint">Loading...</span></div></div>`;
    } else if (st?.error) {
      bodyHtml = `<div class="listing-body"><div class="empty-state compact"><span class="hint">${escapeHtml(st.error)}</span></div></div>`;
    } else if (st) {
      bodyHtml = `<div class="listing-body">${renderRoomsForListing(id, st.rooms, st.positions)}</div>`;
    } else {
      bodyHtml = '';
    }

    const openAttr = openListingIds.has(id) ? ' open' : '';
    return `
      <details class="listing" data-listing-id="${escapeHtml(id)}"${openAttr}>
        <summary class="listing-summary">
          ${CHEVRON_SVG}
          <div class="listing-info">
            <div class="listing-title">${address}</div>
            ${subtitleBits.length ? `<div class="listing-subtitle">${escapeHtml(subtitleBits.join(' \u00b7 '))}</div>` : ''}
          </div>
          <button class="listing-open-btn" data-action="open-listing" data-listing-id="${escapeHtml(id)}" title="Open in tab">\u2197</button>
        </summary>
        ${bodyHtml}
      </details>
    `;
  }

  function renderRoomsForListing(listingId, rooms, positions) {
    if (!rooms || rooms.length === 0) {
      return `
        <div class="empty-state compact">
          <p>No rooms yet</p>
          <span class="hint">Hover photos on the listing to add them</span>
        </div>
      `;
    }

    const cards = rooms.map(room => renderRoomCard(listingId, room, positions)).join('');
    const totalHtml = renderTotalArea(rooms);
    return `<div class="room-list">${cards}</div>${totalHtml}`;
  }

  function renderRoomCard(listingId, room, positions) {
    const isAnalyzing = analyzingRoomIds.has(room.id);
    const hasPhotos = room.photoUrls.length > 0;
    const isMulti = room.photoUrls.length > 1;
    const hasEstimate = room.estimatedSqft !== null && room.estimatedSqft !== undefined;
    const hasValidEstimate = hasEstimate && !(isMulti && room.pipeline === 'single') && !room.outdated;

    let sqftHtml;
    if (isAnalyzing) {
      sqftHtml = `
        <button class="room-sqft-btn analyzing" disabled>
          <span class="spinner-sm"></span>
        </button>
      `;
    } else if (hasValidEstimate) {
      sqftHtml = `
        <button class="room-sqft-btn has-result" data-action="analyze" data-listing-id="${escapeHtml(listingId)}" data-room-id="${escapeHtml(room.id)}"
                title="Click to re-estimate">
          ${room.estimatedSqft} <span class="unit">sqft</span>
        </button>
      `;
    } else if (hasEstimate && hasPhotos) {
      sqftHtml = `
        <button class="room-sqft-btn outdated" data-action="analyze" data-listing-id="${escapeHtml(listingId)}" data-room-id="${escapeHtml(room.id)}"
                title="Click to re-estimate">
          ${room.estimatedSqft} <span class="unit">sqft</span>
        </button>
      `;
    } else if (hasPhotos) {
      sqftHtml = `
        <button class="room-sqft-btn placeholder" data-action="analyze" data-listing-id="${escapeHtml(listingId)}" data-room-id="${escapeHtml(room.id)}"
                title="Click to estimate sq ft">
          <span class="analyze-label">Analyze</span>
          <span class="unknown">???</span> <span class="unit">sqft</span>
        </button>
      `;
    } else {
      sqftHtml = `<span class="room-sqft-btn disabled">&mdash; <span class="unit">sqft</span></span>`;
    }

    const photoCount = room.photoUrls.length;
    const photoLabel = photoCount === 1 ? '1 photo' : `${photoCount} photos`;

    return `
      <div class="room-card" data-room-id="${escapeHtml(room.id)}">
        <div class="room-header">
          <input class="room-name" type="text" value="${escapeHtml(room.name)}"
                 data-action="rename" data-listing-id="${escapeHtml(listingId)}" data-room-id="${escapeHtml(room.id)}" />
          ${hasPhotos ? `<span class="room-photo-count">${photoLabel}</span>` : ''}
          ${sqftHtml}
          ${hasPhotos ? `<button class="room-clear" data-action="clear-photos" data-listing-id="${escapeHtml(listingId)}" data-room-id="${escapeHtml(room.id)}" title="Clear all photos">Clear</button>` : ''}
          <button class="room-delete" data-action="delete" data-listing-id="${escapeHtml(listingId)}" data-room-id="${escapeHtml(room.id)}"
                  title="Delete room">&times;</button>
        </div>
      </div>
    `;
  }

  function renderTotalArea(rooms) {
    const validRooms = rooms.filter(r => {
      if (r.estimatedSqft === null || r.estimatedSqft === undefined) return false;
      if (r.outdated) return false;
      if (r.photoUrls.length > 1 && r.pipeline === 'single') return false;
      return true;
    });

    if (!validRooms.length) return '';
    const total = validRooms.reduce((sum, r) => sum + r.estimatedSqft, 0);
    return `
      <div class="total-area">
        <span class="total-label">Total (${validRooms.length} room${validRooms.length > 1 ? 's' : ''})</span>
        <span class="total-value">${total} <span class="unit">sqft</span></span>
      </div>
    `;
  }

  // ── Event handling (delegated) ──

  function attachDelegatedListeners() {
    listingsEl.addEventListener('toggle', (e) => {
      const details = e.target.closest?.('details.listing');
      if (!details) return;
      const listingId = details.dataset.listingId;
      if (!listingId) return;
      if (details.open) {
        openListingIds.add(listingId);
        void ensureListingStateLoaded(listingId);
      } else {
        openListingIds.delete(listingId);
      }
    }, true);

    listingsEl.addEventListener('click', (e) => {
      const el = e.target.closest?.('[data-action]');
      if (!el) return;
      const action = el.dataset.action;

      if (action === 'open-listing') {
        e.preventDefault();
        e.stopPropagation();
        const listingId = el.dataset.listingId;
        const summary = listingSummaries.find(l => l.id === listingId);
        const url = summary?.url;
        if (!url) return;
        chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
          if (tab) chrome.tabs.update(tab.id, { url });
        });
        return;
      }

      if (action === 'analyze') {
        void handleAnalyze(el.dataset.listingId, el.dataset.roomId);
        return;
      }

      if (action === 'delete') {
        void handleDelete(el.dataset.listingId, el.dataset.roomId);
        return;
      }

      if (action === 'clear-photos') {
        void handleClearPhotos(el.dataset.listingId, el.dataset.roomId);
        return;
      }
    });

    listingsEl.addEventListener('change', (e) => {
      const input = e.target.closest?.('[data-action="rename"]');
      if (!input) return;
      void handleRename(input.dataset.listingId, input.dataset.roomId, input.value);
    });

    listingsEl.addEventListener('keydown', (e) => {
      const input = e.target.closest?.('[data-action="rename"]');
      if (!input) return;
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      }
    });
  }

  async function handleAnalyze(listingId, roomId) {
    if (!listingId || !roomId) return;
    analyzingRoomIds.add(roomId);
    render();
    try {
      const res = await chrome.runtime.sendMessage({ type: 'ANALYZE_ROOM', listingId, roomId });
      if (!res?.success) {
        if (res?.errorCode === 'NO_CUDA_MULTI_UNAVAILABLE') {
          await handleNoCudaAnalyzeFallback(listingId, roomId, res);
          return;
        }
        console.error('[SleepEasy SidePanel] Analyze failed:', res?.error || 'Unknown error');
        window.alert(`Analyze failed: ${res?.error || 'Unknown error'}`);
      }
    } catch (err) {
      console.error('[SleepEasy SidePanel] Analyze failed:', err);
      window.alert(`Analyze failed: ${err?.message || 'Unknown error'}`);
    } finally {
      analyzingRoomIds.delete(roomId);
      render();
    }
  }

  async function handleNoCudaAnalyzeFallback(listingId, roomId, response) {
    const promptRequired = response?.promptRequired !== false;
    const guidance = 'Multi-photo DUSt3R needs CUDA in this self-hosted release. Use Single-image mode in Settings.';

    if (promptRequired) {
      const accepted = window.confirm(
        'CUDA was not detected for multi-photo DUSt3R.\n\nEnable Single-image mode now and retry this room?'
      );
      if (accepted) {
        await updateBackendConfig({
          analysisMode: 'single-image',
          noCudaPromptHandled: true,
        });
        const retry = await chrome.runtime.sendMessage({ type: 'ANALYZE_ROOM', listingId, roomId });
        if (!retry?.success) {
          if (retry?.errorCode === 'NO_CUDA_MULTI_UNAVAILABLE') {
            window.alert(guidance);
            return;
          }
          throw new Error(retry?.error || 'Retry failed');
        }
        return;
      }

      await updateBackendConfig({ noCudaPromptHandled: true });
    }

    window.alert(guidance);
  }

  async function handleDelete(listingId, roomId) {
    if (!listingId || !roomId) return;
    try {
      const res = await chrome.runtime.sendMessage({ type: 'DELETE_ROOM', listingId, roomId });
      if (!res?.success) console.error('[SleepEasy SidePanel] Delete failed:', res?.error);
    } catch (err) {
      console.error('[SleepEasy SidePanel] Delete failed:', err);
    }
  }

  async function handleClearPhotos(listingId, roomId) {
    if (!listingId || !roomId) return;
    const st = listingState.get(listingId);
    const room = st?.rooms?.find(r => r.id === roomId);
    if (!room?.photoUrls?.length) return;
    try {
      for (const photoUrl of room.photoUrls) {
        await chrome.runtime.sendMessage({ type: 'REMOVE_PHOTO_FROM_ROOM', listingId, roomId, photoUrl });
      }
    } catch (err) {
      console.error('[SleepEasy SidePanel] Clear photos failed:', err);
    }
  }

  async function handleRename(listingId, roomId, name) {
    if (!listingId || !roomId) return;
    if (!name || !name.trim()) return;
    try {
      const res = await chrome.runtime.sendMessage({ type: 'RENAME_ROOM', listingId, roomId, name: name.trim() });
      if (!res?.success) console.error('[SleepEasy SidePanel] Rename failed:', res?.error);
    } catch (err) {
      console.error('[SleepEasy SidePanel] Rename failed:', err);
    }
  }

  // ── Util ──

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── Start ──

  initialize();
})();
