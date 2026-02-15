/**
 * SleepEasy Area Analysis - Photo Overlay
 *
 * Injects hover action buttons (Scan, Add to Room) and persistent annotation
 * badges onto StreetEasy listing photos.
 *
 * No images are downloaded or displayed outside the page context.
 */

(function () {
  'use strict';

  const OVERLAY_CLASS = 'sleepsy-overlay';
  const BADGE_CLASS = 'sleepsy-badge';
  const DROPDOWN_CLASS = 'sleepsy-dropdown';

  let listingContext = null;
  let currentListingId = null;
  let currentListingUrl = null;
  let currentAddress = null;
  let annotations = []; // PhotoAnnotation[]
  let activeOverlay = null; // currently shown overlay element
  let activeDropdown = null; // currently open room dropdown
  let processedPhotos = new WeakSet(); // track which photo elements have listeners
  let cleanupObserver = null;
  let navigationInterval = null;
  let currentPageKey = null;

  // ── Initialize ──

  function initialize() {
    listingContext = new window.SleepEasyListingContext();
    window._sleepEasyListingContext = listingContext;

    currentPageKey = getPageKey();
    currentListingId = listingContext.getListingId();

    if (!currentListingId) return;

    currentListingUrl = listingContext.getListingUrl();
    currentAddress = listingContext.getAddress();

    // Set up message listener
    window.SleepEasyBridge.onMessage(handleIncomingMessage);

    // Load existing annotations
    loadAnnotations();

    // Scan for photos and attach overlays
    scanAndAttach();

    // Watch for DOM changes (carousel navigation, lazy loading)
    cleanupObserver = listingContext.observePhotoChanges(debounce(scanAndAttach, 300));

    // Watch for SPA navigation
    navigationInterval = setInterval(checkNavigation, 1000);
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
    // Re-initialize after a short delay for SPA render
    setTimeout(initialize, 500);
  }

  // ── Cleanup ──

  function cleanup() {
    hideOverlay();
    hideDropdown();

    // Remove all badges
    document.querySelectorAll(`.${BADGE_CLASS}`).forEach(el => el.remove());

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
  }

  // ── Load annotations from storage ──

  async function loadAnnotations() {
    if (!currentListingId) return;
    try {
      const response = await window.SleepEasyBridge.getAnnotations(currentListingId);
      if (response?.success) {
        annotations = response.annotations || [];
        refreshBadges();
      }
    } catch {
      // Storage may not be available yet
    }
  }

  // ── Incoming messages from service worker ──

  function handleIncomingMessage(msg) {
    if (msg.type === 'UPDATE_ANNOTATIONS' && msg.listingId === currentListingId) {
      annotations = msg.annotations || [];
      refreshBadges();
    }
  }

  // ── Scan DOM for photos and attach listeners ──

  function scanAndAttach() {
    if (!listingContext) return;

    const photos = listingContext.getPhotoElements();
    for (const { element, url } of photos) {
      if (processedPhotos.has(element)) continue;
      processedPhotos.add(element);
      attachPhotoListeners(element, url);
    }

    refreshBadges();
  }

  // ── Attach hover listeners to a photo element ──

  function attachPhotoListeners(photoEl, photoUrl) {
    const container = listingContext.getPhotoContainer(photoEl);

    // Ensure container is positioned for overlay placement
    const style = window.getComputedStyle(container);
    if (style.position === 'static') {
      container.style.position = 'relative';
    }

    photoEl.addEventListener('mouseenter', () => {
      showOverlay(container, photoUrl);
    });

    container.addEventListener('mouseleave', (e) => {
      // Don't hide if moving to the overlay itself or dropdown
      if (e.relatedTarget && (
        e.relatedTarget.closest?.(`.${OVERLAY_CLASS}`) ||
        e.relatedTarget.closest?.(`.${DROPDOWN_CLASS}`)
      )) return;
      hideOverlay();
      hideDropdown();
    });
  }

  // ── Show hover overlay with action buttons ──

  function showOverlay(container, photoUrl) {
    hideOverlay();

    const overlay = document.createElement('div');
    overlay.className = OVERLAY_CLASS;
    overlay.innerHTML = `
      <button class="sleepsy-btn sleepsy-scan-btn" data-photo-url="${escapeAttr(photoUrl)}">
        <span class="sleepsy-btn-icon">&#9634;</span> Scan
      </button>
      <button class="sleepsy-btn sleepsy-add-btn" data-photo-url="${escapeAttr(photoUrl)}">
        <span class="sleepsy-btn-icon">+</span> Room
      </button>
    `;

    // Scan button
    overlay.querySelector('.sleepsy-scan-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      await handleScan(photoUrl, overlay);
    });

    // Add to Room button
    overlay.querySelector('.sleepsy-add-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      await showRoomDropdown(container, photoUrl, e.target.closest('.sleepsy-btn'));
    });

    // Prevent overlay from propagating clicks to the gallery
    overlay.addEventListener('click', (e) => {
      e.stopPropagation();
    });

    container.appendChild(overlay);
    activeOverlay = overlay;
  }

  function hideOverlay() {
    if (activeOverlay) {
      activeOverlay.remove();
      activeOverlay = null;
    }
  }

  // ── Scan handler ──

  async function handleScan(photoUrl, overlay) {
    if (!currentListingId) return;

    // Show loading state
    const scanBtn = overlay?.querySelector('.sleepsy-scan-btn');
    if (scanBtn) {
      scanBtn.disabled = true;
      scanBtn.innerHTML = '<span class="sleepsy-spinner"></span> Scanning...';
    }

    try {
      await window.SleepEasyBridge.scanPhoto(
        currentListingId,
        currentListingUrl,
        currentAddress,
        photoUrl
      );
      // Annotations will be updated via UPDATE_ANNOTATIONS message
    } catch (err) {
      console.error('[SleepEasy] Scan failed:', err);
      if (scanBtn) {
        scanBtn.disabled = false;
        scanBtn.innerHTML = '<span class="sleepsy-btn-icon">&#9634;</span> Scan';
      }
    }
  }

  // ── Room dropdown ──

  async function showRoomDropdown(container, photoUrl, anchorBtn) {
    hideDropdown();

    if (!currentListingId) return;

    // Fetch current rooms
    const response = await window.SleepEasyBridge.getRooms(currentListingId);
    const rooms = response?.rooms || [];

    const dropdown = document.createElement('div');
    dropdown.className = DROPDOWN_CLASS;

    let itemsHtml = '';
    for (const room of rooms) {
      const hasPhoto = room.photoUrls.includes(photoUrl);
      itemsHtml += `
        <button class="sleepsy-dropdown-item${hasPhoto ? ' active' : ''}"
                data-room-id="${room.id}" data-photo-url="${escapeAttr(photoUrl)}">
          ${escapeHtml(room.name)}
          ${hasPhoto ? ' &#10003;' : ''}
        </button>
      `;
    }

    itemsHtml += `
      <button class="sleepsy-dropdown-item sleepsy-new-room"
              data-photo-url="${escapeAttr(photoUrl)}">
        + New Room
      </button>
    `;

    dropdown.innerHTML = itemsHtml;

    // Position below the anchor button
    if (anchorBtn) {
      const btnRect = anchorBtn.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      dropdown.style.top = `${btnRect.bottom - containerRect.top + 4}px`;
      dropdown.style.right = `${containerRect.right - btnRect.right}px`;
    }

    // Event listeners
    dropdown.querySelectorAll('.sleepsy-dropdown-item').forEach(item => {
      item.addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        const roomId = item.dataset.roomId;

        if (item.classList.contains('sleepsy-new-room')) {
          // Create new room and add photo
          const res = await window.SleepEasyBridge.createRoom(
            currentListingId, currentListingUrl, currentAddress
          );
          if (res?.success && res.room) {
            await window.SleepEasyBridge.addPhotoToRoom(
              currentListingId, currentListingUrl, currentAddress,
              photoUrl, res.room.id
            );
          }
        } else if (item.classList.contains('active')) {
          // Already in this room — remove
          await window.SleepEasyBridge.removePhotoFromRoom(
            currentListingId, photoUrl, roomId
          );
        } else {
          // Add to existing room
          await window.SleepEasyBridge.addPhotoToRoom(
            currentListingId, currentListingUrl, currentAddress,
            photoUrl, roomId
          );
        }

        hideDropdown();
      });
    });

    dropdown.addEventListener('click', e => e.stopPropagation());

    container.appendChild(dropdown);
    activeDropdown = dropdown;

    // Close dropdown on outside click
    setTimeout(() => {
      document.addEventListener('click', closeDropdownOnOutsideClick, { once: true });
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

  // ── Annotation badges ──

  function refreshBadges() {
    if (!listingContext) return;

    // Remove old badges
    document.querySelectorAll(`.${BADGE_CLASS}`).forEach(el => el.remove());

    if (annotations.length === 0) return;

    // Map photoUrl -> annotation for quick lookup
    const annotationMap = new Map();
    for (const ann of annotations) {
      annotationMap.set(ann.photoUrl, ann);
    }

    // Find current photos and add badges
    const photos = listingContext.getPhotoElements();
    for (const { element, url } of photos) {
      const ann = annotationMap.get(url);
      if (!ann) continue;

      const container = listingContext.getPhotoContainer(element);
      const style = window.getComputedStyle(container);
      if (style.position === 'static') {
        container.style.position = 'relative';
      }

      const badge = document.createElement('div');
      badge.className = BADGE_CLASS;

      const sqftText = ann.sqft !== null ? `${ann.sqft} sqft` : '???';
      badge.innerHTML = `
        <span class="sleepsy-badge-text">${escapeHtml(ann.roomName)}</span>
        <span class="sleepsy-badge-sqft">${sqftText}</span>
        <button class="sleepsy-badge-remove" data-room-id="${ann.roomId}"
                data-photo-url="${escapeAttr(url)}" title="Remove from room">&times;</button>
      `;

      // Remove button
      badge.querySelector('.sleepsy-badge-remove').addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await window.SleepEasyBridge.removePhotoFromRoom(
          currentListingId, url, ann.roomId
        );
      });

      badge.addEventListener('click', e => e.stopPropagation());

      container.appendChild(badge);
    }
  }

  // ── Utility ──

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escapeAttr(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;');
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
