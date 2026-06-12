/**
 * SleepEasy Area Analysis - Photo Overlay
 *
 * Hover overlay on StreetEasy listing photos with integrated pill UI:
 * - Unassigned photo: "Add to room ▾"
 * - Assigned photo: "Room Name | ??? sqft" (click sqft to analyze, click room to reassign)
 * - Analyzing: "Room Name | [spinner]"
 * - Has result: "Room Name | 245 sqft"
 *
 * Room assignment dropdown: pick existing room, create a room, rename rooms.
 */

(function () {
  'use strict';

  const OVERLAY_CLASS = 'sleepsy-overlay';
  const DROPDOWN_CLASS = 'sleepsy-dropdown';

  let listingContext = null;
  let currentListingId = null;
  let currentListingUrl = null;
  let currentAddress = null;
  let annotations = [];
  let activeOverlay = null;
  let activeOverlayPhotoUrl = null;
  let activeOverlayContainer = null;
  let activeDropdown = null;
  let processedPhotos = new WeakSet();
  let cleanupObserver = null;
  let currentPageKey = null;
  let lastUsedRoomId = null;
  let analyzingRoomId = null;

  // ── Initialize ──

  function initialize() {
    listingContext = new window.SleepEasyListingContext();
    window._sleepEasyListingContext = listingContext;

    currentPageKey = getPageKey();
    currentListingId = listingContext.getListingId();

    if (!currentListingId) return;

    currentListingUrl = listingContext.getListingUrl();
    currentAddress = listingContext.getAddress();

    window.SleepEasyBridge.onMessage(handleIncomingMessage);

    loadAnnotations();
    scanAndAttach();

    cleanupObserver = listingContext.observePhotoChanges(debounce(scanAndAttach, 300));

    window.addEventListener('popstate', checkNavigation);
    window.addEventListener('__sleepEasyNav', checkNavigation);
  }

  function getPageKey() {
    try {
      const u = new URL(window.location.href);
      return `${u.origin}${u.pathname}`;
    } catch {
      return window.location.pathname;
    }
  }

  function checkNavigation() {
    const newKey = getPageKey();
    if (newKey === currentPageKey) return;
    cleanup();
    currentPageKey = newKey;
    setTimeout(initialize, 500);
  }

  // ── Cleanup ──

  function cleanup() {
    hideOverlay();
    hideDropdown();

    if (cleanupObserver) {
      cleanupObserver();
      cleanupObserver = null;
    }

    if (listingContext) {
      listingContext.cleanup();
      listingContext = null;
    }

    window._sleepEasyListingContext = null;
    processedPhotos = new WeakSet();
    annotations = [];
    currentListingId = null;
    lastUsedRoomId = null;
    analyzingRoomId = null;
  }

  // ── Load annotations from storage ──

  async function loadAnnotations() {
    if (!currentListingId) return;
    try {
      const response = await window.SleepEasyBridge.getAnnotations(currentListingId);
      if (response?.success) {
        annotations = response.annotations || [];
      }
    } catch {
      // Storage may not be available yet
    }
  }

  // ── Incoming messages from service worker ──

  function handleIncomingMessage(msg) {
    if (msg.type === 'UPDATE_ANNOTATIONS' && msg.listingId === currentListingId) {
      annotations = msg.annotations || [];
      if (activeOverlay && activeOverlayPhotoUrl && activeOverlayContainer) {
        showOverlay(activeOverlayContainer, activeOverlayPhotoUrl);
      }
    }
  }

  // ── Scan DOM for photos and attach listeners ──

  function scanAndAttach() {
    if (!listingContext) return;

    const photos = listingContext.getPhotoElements();
    const positionsToSend = {};

    for (const { element, url, position } of photos) {
      if (position) positionsToSend[url] = position;
      if (processedPhotos.has(element)) continue;
      processedPhotos.add(element);
      attachPhotoListeners(element, url);
    }

    if (currentListingId && Object.keys(positionsToSend).length > 0) {
      window.SleepEasyBridge.setPhotoPositions(currentListingId, positionsToSend);
    }
  }

  // ── Attach hover listeners to a photo element ──

  function attachPhotoListeners(photoEl, photoUrl) {
    const container = listingContext.getPhotoContainer(photoEl);

    const style = window.getComputedStyle(container);
    if (style.position === 'static') {
      container.style.position = 'relative';
    }

    const maybeShowOverlay = () => {
      if (
        activeOverlay &&
        activeOverlayPhotoUrl === photoUrl &&
        activeOverlayContainer === container
      ) {
        return;
      }
      showOverlay(container, photoUrl);
    };

    photoEl.addEventListener('mouseenter', maybeShowOverlay);
    container.addEventListener('mouseenter', maybeShowOverlay);

    container.addEventListener('mouseleave', (e) => {
      if (e.relatedTarget && (
        e.relatedTarget.closest?.(`.${OVERLAY_CLASS}`) ||
        e.relatedTarget.closest?.(`.${DROPDOWN_CLASS}`)
      )) return;
      hideOverlay();
      hideDropdown();
    });
  }

  // ── Annotation lookup ──

  function getPhotoAnnotation(photoUrl) {
    return annotations.find(a => a.photoUrl === photoUrl) || null;
  }

  // ── Show hover overlay ──

  function showOverlay(container, photoUrl) {
    hideOverlay();

    const ann = getPhotoAnnotation(photoUrl);
    const isAssigned = !!ann;

    const overlay = document.createElement('div');
    overlay.className = OVERLAY_CLASS;

    if (!isAssigned) {
      // Unassigned: single pill with "Add to room"
      overlay.innerHTML = `
        <div class="sleepsy-actions">
          <div class="sleepsy-pill sleepsy-unassigned">
            <button class="sleepsy-seg sleepsy-room-btn">Add to room &#9662;</button>
          </div>
        </div>
      `;

      const roomBtn = overlay.querySelector('.sleepsy-room-btn');
      roomBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await showRoomDropdown(container, photoUrl, roomBtn);
      });
    } else {
      // Assigned: integrated pill [Room Name | sqft]
      const isAnalyzing = analyzingRoomId === ann.roomId;
      const isMulti = ann.photoCount > 1;
      const hasEstimate = ann.sqft !== null && ann.sqft !== undefined;
      const hasValidEstimate = hasEstimate && !(isMulti && ann.pipeline === 'single') && !ann.outdated;

      let sqftContent;
      let sqftDisabled = false;
      if (isAnalyzing) {
        sqftContent = '<span class="sleepsy-spinner"></span>';
        sqftDisabled = true;
      } else if (hasValidEstimate) {
        sqftContent = `<span class="sleepsy-sqft-value">${ann.sqft}</span> <span class="sleepsy-sqft-unit">sqft</span>`;
      } else if (hasEstimate) {
        sqftContent = `<span class="sleepsy-sqft-value" style="opacity:0.5">${ann.sqft}</span> <span class="sleepsy-sqft-unit">sqft</span>`;
      } else {
        sqftContent = `<span class="sleepsy-analyze-label">Analyze</span> <span class="sleepsy-sqft-unknown">???</span> <span class="sleepsy-sqft-unit">sqft</span>`;
      }

      const roomName = escapeHtml(ann.roomName);
      const countHtml = ann.photoCount > 1
        ? ` <span class="sleepsy-room-count">&middot; ${ann.photoCount}</span>`
        : '';

      overlay.innerHTML = `
        <div class="sleepsy-actions">
          <div class="sleepsy-pill">
            <button class="sleepsy-seg sleepsy-room-btn">
              <span class="sleepsy-room-label">${roomName}${countHtml}</span> &#9662;
            </button>
            <span class="sleepsy-div"></span>
            <button class="sleepsy-seg sleepsy-sqft-btn"${sqftDisabled ? ' disabled' : ''}>
              ${sqftContent}
            </button>
          </div>
        </div>
      `;

      const sqftBtn = overlay.querySelector('.sleepsy-sqft-btn');
      sqftBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await handleEstimateRoom(ann.roomId, overlay);
      });

      const roomBtn = overlay.querySelector('.sleepsy-room-btn');
      roomBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await showRoomDropdown(container, photoUrl, roomBtn);
      });
    }

    overlay.addEventListener('click', (e) => e.stopPropagation());

    container.appendChild(overlay);
    activeOverlay = overlay;
    activeOverlayPhotoUrl = photoUrl;
    activeOverlayContainer = container;
  }

  function hideOverlay() {
    if (activeOverlay) {
      activeOverlay.remove();
      activeOverlay = null;
      activeOverlayPhotoUrl = null;
      activeOverlayContainer = null;
    }
  }

  // ── Room estimate handler ──

  async function handleEstimateRoom(roomId, overlay) {
    if (!currentListingId || !roomId) return;

    analyzingRoomId = roomId;
    const btn = overlay?.querySelector('.sleepsy-sqft-btn');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="sleepsy-spinner"></span>';
    }

    try {
      const res = await window.SleepEasyBridge.analyzeRoom(currentListingId, roomId);
      if (!res?.success) {
        if (res?.errorCode === 'NO_CUDA_MULTI_UNAVAILABLE') {
          await handleNoCudaEstimateFallback(roomId);
          return;
        }
        console.error('[SleepEasy] Room estimate failed:', res?.error || 'Unknown error');
        showToast(`Analyze failed: ${res?.error || 'Unknown error'}`);
      }
    } catch (err) {
      console.error('[SleepEasy] Room estimate failed:', err);
      showToast(`Analyze failed: ${err?.message || 'Unknown error'}`);
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span class="sleepsy-analyze-label">Analyze</span> <span class="sleepsy-sqft-unknown">???</span> <span class="sleepsy-sqft-unit">sqft</span>';
      }
    } finally {
      analyzingRoomId = null;
      if (activeOverlay && activeOverlayPhotoUrl && activeOverlayContainer) {
        showOverlay(activeOverlayContainer, activeOverlayPhotoUrl);
      }
    }
  }

  /**
   * Multi-photo DUSt3R needs CUDA. When the backend reports no CUDA, switch
   * to single-image mode once (persisted), notify, and retry.
   */
  async function handleNoCudaEstimateFallback(roomId) {
    const cfg = await window.SleepEasyBridge.send({
      type: 'SET_BACKEND_CONFIG',
      config: {
        analysisMode: 'single-image',
        noCudaPromptHandled: true,
      },
    });
    if (!cfg?.success) {
      throw new Error(cfg?.error || 'Failed to switch analysis mode');
    }

    showToast('No CUDA GPU detected — switched to single-image mode. Estimates measure visible floor only and read low. Change in side panel settings.', { duration: 8000 });

    const retry = await window.SleepEasyBridge.analyzeRoom(currentListingId, roomId);
    if (!retry?.success) {
      if (retry?.errorCode === 'NO_CUDA_MULTI_UNAVAILABLE') {
        showToast('Multi-photo analysis needs a CUDA GPU. Single-image mode is available in side panel settings.');
        return;
      }
      throw new Error(retry?.error || 'Retry failed');
    }
  }

  // ── Room dropdown ──

  async function showRoomDropdown(container, photoUrl, anchorBtn) {
    hideDropdown();

    if (!currentListingId) return;

    const response = await window.SleepEasyBridge.getRooms(currentListingId);
    const rooms = response?.rooms || [];
    const ann = getPhotoAnnotation(photoUrl);
    const currentRoomId = ann?.roomId || null;
    const hasRooms = rooms.length > 0;
    const defaultRoomName = getNextDefaultRoomName(rooms);

    const dropdown = document.createElement('div');
    dropdown.className = DROPDOWN_CLASS;

    dropdown.innerHTML = hasRooms ? `
      <div class="sleepsy-dd-head">
        <div class="sleepsy-dd-title">${currentRoomId ? 'Reassign room' : 'Assign to room'}</div>
        <input class="sleepsy-dd-search" type="text" placeholder="Find room..." autocomplete="off" />
      </div>
      <div class="sleepsy-dd-list"></div>
      <div class="sleepsy-dd-divider"></div>
      <button class="sleepsy-dd-new-toggle" type="button">+ New room</button>
      <div class="sleepsy-dd-create sleepsy-dd-create-collapsed">
        <input class="sleepsy-dd-create-input" type="text" placeholder="Room name" autocomplete="off" />
        <button class="sleepsy-dd-create-btn" type="button">Create</button>
      </div>
      ${currentRoomId ? `
        <div class="sleepsy-dd-divider"></div>
        <button class="sleepsy-dd-remove" type="button">Remove from room</button>
      ` : ''}
    ` : `
      <div class="sleepsy-dd-head">
        <div class="sleepsy-dd-title">Create your first room</div>
      </div>
      <div class="sleepsy-dd-create">
        <input class="sleepsy-dd-create-input" type="text" placeholder="Room name" autocomplete="off" />
        <button class="sleepsy-dd-create-btn" type="button">Create</button>
      </div>
    `;

    const listEl = dropdown.querySelector('.sleepsy-dd-list');
    if (listEl) {
      listEl.innerHTML = rooms.map((room) => {
        const isActive = room.id === currentRoomId;
        const isLast = room.id === lastUsedRoomId && !isActive;
        const count = room.photoUrls?.length || 0;
        const meta = count ? `${count} photo${count === 1 ? '' : 's'}` : 'empty';
        return `
          <div class="sleepsy-room-row ${isActive ? 'active' : ''} ${isLast ? 'last-used' : ''}" data-room-id="${escapeHtml(room.id)}" data-room-name="${escapeHtml(room.name)}">
            <button class="sleepsy-room-pick" type="button" ${isActive ? 'disabled' : ''}>
              <span class="sleepsy-room-check">${isActive ? '&#10003;' : ''}</span>
              <span class="sleepsy-room-name">${escapeHtml(room.name)}</span>
              <span class="sleepsy-room-meta">${escapeHtml(meta)}</span>
            </button>
            <button class="sleepsy-room-rename" type="button" title="Rename room" aria-label="Rename room">&#9998;</button>
          </div>
        `;
      }).join('');
    }

    // Position below the anchor button
    if (anchorBtn) {
      const btnRect = anchorBtn.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      dropdown.style.top = `${btnRect.bottom - containerRect.top + 4}px`;
      dropdown.style.right = `${containerRect.right - btnRect.right}px`;
    }

    // Search filter
    const searchInput = dropdown.querySelector('.sleepsy-dd-search');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        const q = searchInput.value.trim().toLowerCase();
        dropdown.querySelectorAll('.sleepsy-room-row').forEach(row => {
          const name = (row.dataset.roomName || '').toLowerCase();
          row.style.display = !q || name.includes(q) ? '' : 'none';
        });
      });
    }

    // Create room
    const createWrap = dropdown.querySelector('.sleepsy-dd-create');
    const createInput = dropdown.querySelector('.sleepsy-dd-create-input');
    const createBtn = dropdown.querySelector('.sleepsy-dd-create-btn');
    const newToggle = dropdown.querySelector('.sleepsy-dd-new-toggle');
    if (createInput) {
      createInput.value = defaultRoomName;
      createInput.addEventListener('focus', () => {
        createInput.select();
      });
    }
    if (newToggle && createWrap) {
      newToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        createWrap.classList.toggle('sleepsy-dd-create-collapsed');
        if (!createWrap.classList.contains('sleepsy-dd-create-collapsed')) {
          createInput.focus();
        }
      });
    }

    async function createRoomAndAssign() {
      const name = createInput.value.trim();
      const res = await window.SleepEasyBridge.createRoom(
        currentListingId, currentListingUrl, currentAddress, name || undefined
      );
      if (res?.success && res.room) {
        lastUsedRoomId = res.room.id;
        await window.SleepEasyBridge.addPhotoToRoom(
          currentListingId, currentListingUrl, currentAddress,
          photoUrl, res.room.id
        );
        hideDropdown();
      }
    }
    createBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      await createRoomAndAssign();
    });
    createInput.addEventListener('keydown', async (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        await createRoomAndAssign();
      }
    });

    // Pick / rename handlers (delegated)
    if (listEl) {
      listEl.addEventListener('click', async (e) => {
        const pickBtn = e.target.closest?.('.sleepsy-room-pick');
        if (pickBtn) {
          e.stopPropagation();
          e.preventDefault();
          const row = pickBtn.closest('.sleepsy-room-row');
          const roomId = row?.dataset?.roomId;
          if (!roomId || roomId === currentRoomId) return;

          lastUsedRoomId = roomId;
          await window.SleepEasyBridge.addPhotoToRoom(
            currentListingId, currentListingUrl, currentAddress,
            photoUrl, roomId
          );
          hideDropdown();
          return;
        }

        const renameBtn = e.target.closest?.('.sleepsy-room-rename');
        if (!renameBtn) return;

        e.stopPropagation();
        e.preventDefault();
        const row = renameBtn.closest('.sleepsy-room-row');
        const roomId = row?.dataset?.roomId;
        const currentName = row?.dataset?.roomName || '';
        if (!row || !roomId) return;

        if (row.classList.contains('editing')) return;
        row.classList.add('editing');

        const nameEl = row.querySelector('.sleepsy-room-name');
        const metaEl = row.querySelector('.sleepsy-room-meta');
        const checkEl = row.querySelector('.sleepsy-room-check');
        const pickEl = row.querySelector('.sleepsy-room-pick');
        if (!nameEl || !pickEl) return;

        const input = document.createElement('input');
        input.className = 'sleepsy-room-rename-input';
        input.type = 'text';
        input.value = currentName;
        input.autocomplete = 'off';

        nameEl.replaceWith(input);
        if (metaEl) metaEl.classList.add('muted');
        if (checkEl) checkEl.textContent = '';

        const finish = async (mode) => {
          row.classList.remove('editing');
          if (metaEl) metaEl.classList.remove('muted');

          const nextName = input.value.trim();
          if (mode === 'save' && nextName && nextName !== currentName) {
            await window.SleepEasyBridge.renameRoom(currentListingId, roomId, nextName);
          }
          hideDropdown();
        };

        input.addEventListener('keydown', async (ke) => {
          if (ke.key === 'Enter') {
            ke.preventDefault();
            await finish('save');
          } else if (ke.key === 'Escape') {
            ke.preventDefault();
            await finish('cancel');
          }
        });

        pickEl.disabled = true;
        input.focus();
        input.select();
      });
    }

    // Remove from room
    const removeBtn = dropdown.querySelector('.sleepsy-dd-remove');
    if (removeBtn) {
      removeBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (!currentRoomId) return;
        await window.SleepEasyBridge.removePhotoFromRoom(currentListingId, photoUrl, currentRoomId);
        hideDropdown();
      });
    }

    dropdown.addEventListener('click', e => e.stopPropagation());

    container.appendChild(dropdown);
    activeDropdown = dropdown;

    setTimeout(() => {
      document.addEventListener('click', closeDropdownOnOutsideClick, { once: true });
    }, 0);

    setTimeout(() => {
      if (!hasRooms) {
        try { createInput.focus(); } catch {}
        return;
      }
      try { searchInput?.focus(); } catch {}
    }, 0);
  }

  function hideDropdown() {
    if (activeDropdown) {
      activeDropdown.remove();
      activeDropdown = null;
    }
  }

  function closeDropdownOnOutsideClick(e) {
    if (activeDropdown && !activeDropdown.contains(e.target)) {
      hideDropdown();
    }
  }

  // ── Utility ──

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── Toast (non-blocking notice; replaces window.alert) ──

  let toastTimer = null;

  function showToast(message, { duration = 5000 } = {}) {
    document.querySelector('.sleepeasy-toast')?.remove();
    if (toastTimer) clearTimeout(toastTimer);

    const toast = document.createElement('div');
    toast.className = 'sleepeasy-toast';
    toast.setAttribute('role', 'status');
    toast.textContent = message;
    document.body.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add('visible'));
    toastTimer = setTimeout(() => {
      toast.classList.remove('visible');
      setTimeout(() => toast.remove(), 200);
    }, duration);
  }

  function getNextDefaultRoomName(rooms) {
    let max = 0;
    for (const room of rooms || []) {
      const name = String(room?.name || '').trim();
      const m = name.match(/^room\s*#?\s*(\d+)$/i);
      if (!m) continue;
      const n = parseInt(m[1], 10);
      if (Number.isFinite(n) && n > max) max = n;
    }
    return `Room #${max + 1}`;
  }

  function debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  // ── Start ──

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }
})();
