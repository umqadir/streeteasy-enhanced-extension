/**
 * SleepEasy side panel: photo-level square-footage estimates.
 */

(function () {
  'use strict';

  let listingSummaries = [];
  const listingState = new Map();
  const openListingIds = new Set();
  const analyzingPhotoIds = new Set();
  let activeListingId = null;
  let historyRefreshTimer = null;
  let noticeTimer = null;

  const listingsEl = document.getElementById('listings');
  const noticeEl = document.getElementById('panel-notice');
  const settingsToggle = document.getElementById('settings-toggle');
  const settingsPanel = document.getElementById('settings-panel');
  const backendUrlEl = document.getElementById('backend-url');
  const backendDeviceEl = document.getElementById('backend-device');
  const analysisBackendEl = document.getElementById('analysis-backend');
  const backendSaveEl = document.getElementById('backend-save');
  const backendCheckEl = document.getElementById('backend-check');
  const backendStatusEl = document.getElementById('backend-status');

  async function initialize() {
    attachDelegatedListeners();
    initSettingsToggle();
    listenForUpdates();
    initBackendControls();
    await Promise.all([refreshAll({ initial: true }), loadBackendConfig()]);
  }

  function initSettingsToggle() {
    if (!settingsToggle || !settingsPanel) return;
    settingsToggle.addEventListener('click', () => {
      const isHidden = settingsPanel.classList.toggle('hidden');
      settingsToggle.classList.toggle('active', !isHidden);
    });
  }

  async function getActiveTabListingContext() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return null;
    try {
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: 'GET_LISTING_CONTEXT',
        target: 'content',
      });
      return response?.listingId ? response : null;
    } catch {
      return null;
    }
  }

  async function loadHistorySummaries() {
    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_HISTORY' });
      return res?.success ? (res.history || []) : [];
    } catch {
      return [];
    }
  }

  async function refreshAll({ initial = false } = {}) {
    const ctx = await getActiveTabListingContext();
    activeListingId = ctx?.listingId || null;

    listingSummaries = await loadHistorySummaries();
    if (activeListingId && !listingSummaries.some(l => l.id === activeListingId)) {
      listingSummaries = [{
        id: activeListingId,
        url: ctx?.listingUrl || '',
        address: ctx?.address || activeListingId,
        updatedAt: Date.now(),
        photoCount: null,
        analyzedPhotoCount: null,
      }, ...listingSummaries];
    }

    if (initial) {
      const defaultOpen = activeListingId || listingSummaries[0]?.id || null;
      if (defaultOpen) openListingIds.add(defaultOpen);
    }

    render();
    for (const id of openListingIds) void ensureListingStateLoaded(id);
  }

  function initBackendControls() {
    if (!backendSaveEl || !backendCheckEl) return;
    backendSaveEl.addEventListener('click', () => void saveBackendConfig());
    backendCheckEl.addEventListener('click', () => void checkBackendHealth());
  }

  function setBackendStatus(message, level = 'info') {
    if (!backendStatusEl) return;
    backendStatusEl.textContent = message || '';
    backendStatusEl.classList.remove('ok', 'warn', 'error');
    if (['ok', 'warn', 'error'].includes(level)) backendStatusEl.classList.add(level);
  }

  function applyBackendConfigToForm(config) {
    if (!config) return;
    if (backendUrlEl) backendUrlEl.value = config.baseUrl || 'http://127.0.0.1:8787';
    if (backendDeviceEl) backendDeviceEl.value = config.devicePolicy || 'auto';
    if (analysisBackendEl) analysisBackendEl.value = config.analysisBackend || 'auto';
  }

  function readBackendConfigFromForm() {
    return {
      baseUrl: (backendUrlEl?.value || '').trim(),
      devicePolicy: backendDeviceEl?.value || 'auto',
      analysisBackend: analysisBackendEl?.value || 'auto',
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
      setBackendStatus(`Connected${device}`, 'ok');
      return;
    }
    setBackendStatus(`Unavailable: ${health.error || 'unknown'}`, 'error');
  }

  async function saveBackendConfig() {
    setBackendStatus('Saving...');
    try {
      const res = await chrome.runtime.sendMessage({
        type: 'SET_BACKEND_CONFIG',
        config: readBackendConfigFromForm(),
      });
      if (!res?.success) throw new Error(res?.error || 'Failed to save backend config');
      applyBackendConfigToForm(res.config || {});
      renderHealthStatus(res.health);
    } catch (err) {
      setBackendStatus(`Failed: ${err?.message || 'unknown'}`, 'error');
    }
  }

  async function checkBackendHealth() {
    setBackendStatus('Checking...');
    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_BACKEND_HEALTH' });
      if (!res?.success) throw new Error(res?.error || 'unknown');
      renderHealthStatus(res.health);
    } catch (err) {
      setBackendStatus(`Failed: ${err?.message || 'unknown'}`, 'error');
    }
  }

  async function ensureListingStateLoaded(listingId) {
    if (!listingId) return;
    const existing = listingState.get(listingId);
    if (existing && (existing.loading || existing.photos)) return;

    listingState.set(listingId, { loading: true, error: null, listing: null, photos: [], positions: {} });
    render();

    try {
      const res = await chrome.runtime.sendMessage({ type: 'GET_LISTING_STATE', listingId });
      if (!res?.success) {
        listingState.set(listingId, { loading: false, error: res?.error || 'Failed to load', listing: null, photos: [], positions: {} });
        render();
        return;
      }
      listingState.set(listingId, {
        loading: false,
        error: null,
        listing: res.listing || null,
        photos: res.photos || [],
        positions: res.positions || {},
      });
      render();
    } catch (err) {
      listingState.set(listingId, { loading: false, error: err?.message || 'Failed to load', listing: null, photos: [], positions: {} });
      render();
    }
  }

  function listenForUpdates() {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.target !== 'sidepanel') return;

      if (msg.type === 'RUN_INBROWSER_PHOTO' && msg.listingId && msg.photoId) {
        void runInBrowserEstimate(msg.listingId, msg.photoId);
        return;
      }

      if (msg.type !== 'STATE_CHANGED' || !msg.listingId) return;
      const prev = listingState.get(msg.listingId) || { loading: false, error: null, listing: null, photos: [], positions: {} };
      listingState.set(msg.listingId, {
        ...prev,
        loading: false,
        error: null,
        photos: msg.photos || [],
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
      listingSummaries = await loadHistorySummaries();
      if (activeListingId && !listingSummaries.some(l => l.id === activeListingId)) {
        listingSummaries = [{ id: activeListingId, url: '', address: activeListingId, updatedAt: Date.now(), photoCount: null, analyzedPhotoCount: null }, ...listingSummaries];
      }
      render();
    }, 350);
  }

  const CHEVRON_SVG = '<svg class="listing-chevron" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function render() {
    if (!listingSummaries.length) {
      listingsEl.innerHTML = `
        <div class="empty-state">
          <p>No photos yet</p>
          <span class="hint">Hover a listing photo to analyze or label it</span>
        </div>
      `;
      return;
    }

    listingsEl.innerHTML = listingSummaries.map(summary => renderListingSection(summary)).join('');
    listingsEl.querySelectorAll('details.listing').forEach(details => {
      const id = details.dataset.listingId;
      if (id) details.open = openListingIds.has(id);
    });
  }

  function renderListingSection(summary) {
    const id = summary.id;
    const st = listingState.get(id) || null;
    const address = escapeHtml(summary.address || id);
    const date = summary.updatedAt ? new Date(summary.updatedAt).toLocaleDateString() : '';

    const subtitleBits = [];
    if (summary.photoCount != null) subtitleBits.push(summary.photoCount === 1 ? '1 photo' : `${summary.photoCount} photos`);
    if (summary.analyzedPhotoCount) subtitleBits.push(`${summary.analyzedPhotoCount} analyzed`);
    if (date) subtitleBits.push(date);

    let bodyHtml = '';
    if (st?.loading) {
      bodyHtml = `<div class="listing-body"><div class="empty-state compact"><span class="hint">Loading...</span></div></div>`;
    } else if (st?.error) {
      bodyHtml = `<div class="listing-body"><div class="empty-state compact"><span class="hint">${escapeHtml(st.error)}</span></div></div>`;
    } else if (st) {
      bodyHtml = `<div class="listing-body">${renderPhotosForListing(id, st.photos, st.positions)}</div>`;
    }

    return `
      <details class="listing" data-listing-id="${escapeHtml(id)}"${openListingIds.has(id) ? ' open' : ''}>
        <summary class="listing-summary">
          ${CHEVRON_SVG}
          <div class="listing-info">
            <div class="listing-title">${address}</div>
            ${subtitleBits.length ? `<div class="listing-subtitle">${escapeHtml(subtitleBits.join(' · '))}</div>` : ''}
          </div>
          <button class="listing-open-btn" data-action="open-listing" data-listing-id="${escapeHtml(id)}" title="Open in tab">↗</button>
        </summary>
        ${bodyHtml}
      </details>
    `;
  }

  function renderPhotosForListing(listingId, photos, positions) {
    if (!photos?.length) {
      return `
        <div class="empty-state compact">
          <p>No photos yet</p>
          <span class="hint">Hover a listing photo to analyze or label it</span>
        </div>
      `;
    }

    const sorted = [...photos].sort((a, b) => {
      const ap = positions[a.photoUrl] || Number.MAX_SAFE_INTEGER;
      const bp = positions[b.photoUrl] || Number.MAX_SAFE_INTEGER;
      return ap - bp;
    });
    return `<div class="photo-list">${sorted.map(photo => renderPhotoRow(listingId, photo, positions)).join('')}</div>`;
  }

  function renderPhotoRow(listingId, photo, positions) {
    const isAnalyzing = analyzingPhotoIds.has(photo.id);
    const hasEstimate = photo.estimatedSqft !== null && photo.estimatedSqft !== undefined;
    const position = positions[photo.photoUrl] || null;
    const labelValue = escapeHtml(photo.label || '');

    let sqftHtml;
    if (isAnalyzing) {
      sqftHtml = `<button class="photo-sqft-btn analyzing" disabled><span class="spinner-sm"></span></button>`;
    } else if (hasEstimate) {
      sqftHtml = `
        <button class="photo-sqft-btn has-result" data-action="analyze" data-listing-id="${escapeHtml(listingId)}" data-photo-id="${escapeHtml(photo.id)}" title="Re-analyze photo">
          ${photo.estimatedSqft} <span class="unit">sqft</span>
        </button>
      `;
    } else {
      sqftHtml = `
        <button class="photo-sqft-btn placeholder" data-action="analyze" data-listing-id="${escapeHtml(listingId)}" data-photo-id="${escapeHtml(photo.id)}" title="Analyze photo">
          <span class="analyze-label">Analyze</span>
          <span class="unknown">???</span> <span class="unit">sqft</span>
        </button>
      `;
    }

    return `
      <div class="photo-row" data-photo-id="${escapeHtml(photo.id)}">
        <div class="photo-meta">
          <span class="photo-position">${position ? `Photo ${position}` : 'Photo'}</span>
          <input class="photo-label" type="text" value="${labelValue}" placeholder="Label room"
                 data-action="label" data-listing-id="${escapeHtml(listingId)}" data-photo-id="${escapeHtml(photo.id)}" />
        </div>
        ${sqftHtml}
        <button class="photo-delete" data-action="delete" data-listing-id="${escapeHtml(listingId)}" data-photo-id="${escapeHtml(photo.id)}" title="Remove photo">×</button>
      </div>
    `;
  }

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

      if (el.dataset.action === 'open-listing') {
        e.preventDefault();
        e.stopPropagation();
        const listing = listingSummaries.find(l => l.id === el.dataset.listingId);
        if (!listing?.url) return;
        chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
          if (tab) chrome.tabs.update(tab.id, { url: listing.url });
        });
        return;
      }

      if (el.dataset.action === 'analyze') {
        void handleAnalyze(el.dataset.listingId, el.dataset.photoId);
      } else if (el.dataset.action === 'delete') {
        void handleDelete(el.dataset.listingId, el.dataset.photoId);
      }
    });

    listingsEl.addEventListener('change', (e) => {
      const input = e.target.closest?.('[data-action="label"]');
      if (!input) return;
      void handleLabel(input.dataset.listingId, input.dataset.photoId, input.value);
    });

    listingsEl.addEventListener('keydown', (e) => {
      const input = e.target.closest?.('[data-action="label"]');
      if (!input) return;
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      }
    });
  }

  function showNotice(message, { duration = 7000 } = {}) {
    if (!noticeEl) return;
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeEl.textContent = message;
    noticeEl.classList.remove('hidden');
    noticeTimer = setTimeout(() => noticeEl.classList.add('hidden'), duration);
  }

  async function handleAnalyze(listingId, photoId) {
    if (!listingId || !photoId) return;
    analyzingPhotoIds.add(photoId);
    render();
    let deferred = false;
    try {
      const res = await chrome.runtime.sendMessage({ type: 'ANALYZE_PHOTO', listingId, photoId });
      if (res?.deferred) { deferred = true; return; }
      if (!res?.success) throw new Error(res?.error || 'Unknown error');
    } catch (err) {
      showNotice(`Analyze failed: ${err?.message || 'Unknown error'}`);
    } finally {
      if (!deferred) {
        analyzingPhotoIds.delete(photoId);
        render();
      }
    }
  }

  async function fetchImage(url) {
    const res = await fetch(url, { credentials: 'omit' });
    if (!res.ok) throw new Error(`image fetch ${res.status}`);
    const objUrl = URL.createObjectURL(await res.blob());
    try {
      const img = new Image();
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error('image decode failed'));
        img.src = objUrl;
      });
      if (img.decode) { try { await img.decode(); } catch {} }
      return img;
    } finally {
      setTimeout(() => URL.revokeObjectURL(objUrl), 15000);
    }
  }

  async function getPhoto(listingId, photoId) {
    const st = listingState.get(listingId);
    let photo = st?.photos?.find(p => p.id === photoId);
    if (photo) return photo;
    const res = await chrome.runtime.sendMessage({ type: 'GET_LISTING_STATE', listingId });
    return res?.photos?.find(p => p.id === photoId) || null;
  }

  async function runInBrowserEstimate(listingId, photoId) {
    if (!analyzingPhotoIds.has(photoId)) {
      analyzingPhotoIds.add(photoId);
      render();
    }
    try {
      const photo = await getPhoto(listingId, photoId);
      if (!photo?.photoUrl) throw new Error('Photo is not saved');

      const { estimate } = await import('../lib/sqft/pipeline.js');
      const img = await fetchImage(photo.photoUrl);
      const result = await estimate(img, { tokens: 1200 });
      if (!result?.ok || !Number.isFinite(result.sqft) || result.sqft <= 0) {
        throw new Error('No floor area detected in this photo');
      }

      await chrome.runtime.sendMessage({
        type: 'SET_PHOTO_ESTIMATE',
        listingId,
        photoId,
        sqft: result.sqft,
        pipeline: 'inbrowser',
      });
    } catch (err) {
      showNotice(`Analyze failed: ${err?.message || 'Unknown error'}`);
    } finally {
      analyzingPhotoIds.delete(photoId);
      render();
    }
  }

  async function handleDelete(listingId, photoId) {
    if (!listingId || !photoId) return;
    try {
      const res = await chrome.runtime.sendMessage({ type: 'DELETE_PHOTO', listingId, photoId });
      if (!res?.success) throw new Error(res?.error || 'Delete failed');
    } catch (err) {
      showNotice(err?.message || 'Delete failed');
    }
  }

  async function handleLabel(listingId, photoId, label) {
    if (!listingId || !photoId) return;
    try {
      const res = await chrome.runtime.sendMessage({ type: 'LABEL_PHOTO', listingId, photoId, label: label.trim() });
      if (!res?.success) throw new Error(res?.error || 'Label failed');
    } catch (err) {
      showNotice(err?.message || 'Label failed');
    }
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  initialize();
})();
