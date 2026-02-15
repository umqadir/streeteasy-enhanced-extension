/**
 * SleepEasy Side Panel - Area Analysis
 *
 * Shows room list with photo counts & sqft estimates for the current listing,
 * and a history view of all analyzed listings.
 * No image thumbnails — purely text/metadata.
 */

(function () {
  'use strict';

  // ── State ──

  let currentListingId = null;
  let currentAddress = null;
  let rooms = [];
  let activeView = 'current'; // 'current' | 'history'
  let analyzingRoomId = null; // room currently being analyzed

  // ── DOM refs ──

  const listingInfoEl = document.getElementById('listing-info');
  const roomListEl = document.getElementById('room-list');
  const totalAreaEl = document.getElementById('total-area');
  const currentViewEl = document.getElementById('current-view');
  const historyViewEl = document.getElementById('history-view');
  const listingHistoryEl = document.getElementById('listing-history');

  // ── Init ──

  async function initialize() {
    attachTabListeners();
    listenForUpdates();
    await loadCurrentListing();
  }

  // ── Tab switching ──

  function attachTabListeners() {
    document.querySelectorAll('.tab').forEach(btn => {
      btn.addEventListener('click', () => {
        const view = btn.dataset.view;
        if (view === activeView) return;
        activeView = view;
        document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        if (view === 'current') {
          currentViewEl.hidden = false;
          historyViewEl.hidden = true;
        } else {
          currentViewEl.hidden = true;
          historyViewEl.hidden = false;
          loadHistory();
        }
      });
    });
  }

  // ── Load current listing from active tab ──

  async function loadCurrentListing() {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab) {
        renderEmptyState();
        return;
      }

      // Ask content script for listing context
      let response;
      try {
        response = await chrome.tabs.sendMessage(tab.id, {
          type: 'GET_LISTING_CONTEXT',
          target: 'content',
        });
      } catch {
        // Content script not loaded (wrong page)
        renderEmptyState();
        return;
      }

      if (!response || !response.listingId) {
        renderEmptyState();
        return;
      }

      currentListingId = response.listingId;
      currentAddress = response.address;

      // Get rooms from service worker
      const state = await chrome.runtime.sendMessage({
        type: 'GET_LISTING_STATE',
        listingId: currentListingId,
      });

      rooms = state?.rooms || [];
      renderCurrentView();
    } catch (err) {
      console.error('[SleepEasy SidePanel] Init error:', err);
      renderEmptyState();
    }
  }

  // ── Listen for real-time updates ──

  function listenForUpdates() {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.target !== 'sidepanel') return;

      if (msg.type === 'STATE_CHANGED' && msg.listingId === currentListingId) {
        rooms = msg.rooms || [];
        renderCurrentView();
      }
    });

    // Re-load when active tab changes
    chrome.tabs.onActivated?.addListener(() => {
      loadCurrentListing();
    });
  }

  // ── Render: empty state ──

  function renderEmptyState() {
    currentListingId = null;
    currentAddress = null;
    rooms = [];

    listingInfoEl.innerHTML = '';
    roomListEl.innerHTML = `
      <div class="empty-state">
        <p>No listing detected</p>
        <span class="hint">Navigate to a StreetEasy listing to begin</span>
      </div>
    `;
    totalAreaEl.classList.add('hidden');
    totalAreaEl.innerHTML = '';
  }

  // ── Render: current listing view ──

  function renderCurrentView() {
    renderListingInfo();
    renderRoomList();
    renderTotalArea();
  }

  function renderListingInfo() {
    if (!currentListingId) {
      listingInfoEl.innerHTML = '';
      return;
    }

    listingInfoEl.innerHTML = `
      <div class="listing-address">${escapeHtml(currentAddress || currentListingId)}</div>
    `;
  }

  function renderRoomList() {
    if (rooms.length === 0) {
      roomListEl.innerHTML = `
        <div class="empty-state">
          <p>No rooms yet</p>
          <span class="hint">Hover over listing photos to scan or add to rooms</span>
        </div>
      `;
      return;
    }

    roomListEl.innerHTML = rooms.map(room => renderRoomCard(room)).join('');
    attachRoomListeners();
  }

  function renderRoomCard(room) {
    const photoLabel = room.photoUrls.length === 1
      ? '1 photo'
      : `${room.photoUrls.length} photos`;

    let sqftHtml;
    if (room.estimatedSqft !== null) {
      sqftHtml = `<span class="room-sqft">${room.estimatedSqft} <span class="unit">sqft</span></span>`;
    } else {
      sqftHtml = `<span class="room-sqft placeholder">???</span>`;
    }

    let actionsHtml = '';
    const isAnalyzing = analyzingRoomId === room.id;

    if (room.outdated) {
      actionsHtml = `
        <div class="room-actions">
          <button class="outdated-badge" data-room-id="${room.id}" data-action="analyze">
            &#x26A0; Outdated &mdash; re-analyze
          </button>
        </div>
      `;
    } else if (room.photoUrls.length > 0 && room.estimatedSqft === null) {
      actionsHtml = `
        <div class="room-actions">
          <button class="analyze-btn${isAnalyzing ? ' loading' : ''}" data-room-id="${room.id}" data-action="analyze"${isAnalyzing ? ' disabled' : ''}>
            ${isAnalyzing ? '<span class="spinner-sm"></span> Analyzing...' : 'Analyze'}
          </button>
        </div>
      `;
    }

    return `
      <div class="room-card" data-room-id="${room.id}">
        <div class="room-header">
          <input class="room-name" type="text" value="${escapeHtml(room.name)}"
                 data-room-id="${room.id}" data-action="rename" />
          <button class="room-delete" data-room-id="${room.id}" data-action="delete"
                  title="Delete room">&times;</button>
        </div>
        <div class="room-meta">
          <span class="room-photo-count">${photoLabel}</span>
          ${sqftHtml}
        </div>
        ${actionsHtml}
      </div>
    `;
  }

  function renderTotalArea() {
    const analyzed = rooms.filter(r => r.estimatedSqft !== null);
    if (analyzed.length === 0) {
      totalAreaEl.classList.add('hidden');
      totalAreaEl.innerHTML = '';
      return;
    }

    const total = analyzed.reduce((sum, r) => sum + r.estimatedSqft, 0);
    totalAreaEl.classList.remove('hidden');
    totalAreaEl.innerHTML = `
      <span class="total-label">Total (${analyzed.length} room${analyzed.length > 1 ? 's' : ''})</span>
      <span class="total-value">${total} <span class="unit">sqft</span></span>
    `;
  }

  // ── Room event listeners ──

  function attachRoomListeners() {
    // Analyze / re-analyze buttons
    roomListEl.querySelectorAll('[data-action="analyze"]').forEach(btn => {
      btn.addEventListener('click', () => handleAnalyze(btn.dataset.roomId));
    });

    // Delete buttons
    roomListEl.querySelectorAll('[data-action="delete"]').forEach(btn => {
      btn.addEventListener('click', () => handleDelete(btn.dataset.roomId));
    });

    // Rename inputs
    roomListEl.querySelectorAll('[data-action="rename"]').forEach(input => {
      input.addEventListener('change', () => handleRename(input.dataset.roomId, input.value));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          input.blur();
        }
      });
    });
  }

  async function handleAnalyze(roomId) {
    if (!currentListingId) return;
    analyzingRoomId = roomId;
    renderRoomList(); // show spinner

    await chrome.runtime.sendMessage({
      type: 'ANALYZE_ROOM',
      listingId: currentListingId,
      roomId,
    });

    analyzingRoomId = null;
    // STATE_CHANGED will trigger a re-render
  }

  async function handleDelete(roomId) {
    if (!currentListingId) return;
    await chrome.runtime.sendMessage({
      type: 'DELETE_ROOM',
      listingId: currentListingId,
      roomId,
    });
  }

  async function handleRename(roomId, name) {
    if (!currentListingId || !name.trim()) return;
    await chrome.runtime.sendMessage({
      type: 'RENAME_ROOM',
      listingId: currentListingId,
      roomId,
      name: name.trim(),
    });
  }

  // ── History view ──

  async function loadHistory() {
    const response = await chrome.runtime.sendMessage({ type: 'GET_HISTORY' });
    if (!response?.success) {
      listingHistoryEl.innerHTML = `
        <div class="empty-state">
          <p>No listings analyzed yet</p>
        </div>
      `;
      return;
    }

    const history = response.history || [];
    if (history.length === 0) {
      listingHistoryEl.innerHTML = `
        <div class="empty-state">
          <p>No listings analyzed yet</p>
          <span class="hint">Scan photos on StreetEasy listings to start building your history</span>
        </div>
      `;
      return;
    }

    listingHistoryEl.innerHTML = history.map(item => {
      const date = new Date(item.updatedAt).toLocaleDateString();
      const roomLabel = item.roomCount === 1 ? '1 room' : `${item.roomCount} rooms`;
      const sqftHtml = item.totalSqft
        ? `<div class="history-sqft">${item.totalSqft} <span class="unit">sqft</span></div>`
        : '';

      return `
        <div class="history-item" data-listing-url="${escapeHtml(item.url)}">
          <div class="history-address">${escapeHtml(item.address || item.id)}</div>
          <div class="history-meta">${roomLabel} &middot; ${date}</div>
          ${sqftHtml}
        </div>
      `;
    }).join('');

    // Click to navigate
    listingHistoryEl.querySelectorAll('.history-item').forEach(el => {
      el.addEventListener('click', () => {
        const url = el.dataset.listingUrl;
        if (url) {
          chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
            if (tab) chrome.tabs.update(tab.id, { url });
          });
        }
      });
    });
  }

  // ── Util ──

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── Start ──

  initialize();
})();
