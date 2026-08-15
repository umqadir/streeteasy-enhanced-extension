/**
 * SleepEasy photo overlay.
 *
 * Each listing photo can be analyzed independently. "Label room" is only an
 * optional label for organizing that photo.
 */

(function () {
  'use strict';

  const OVERLAY_CLASS = 'sleepsy-overlay';
  const DROPDOWN_CLASS = 'sleepsy-dropdown';

  let listingContext = null;
  let currentListingId = null;
  let currentListingUrl = null;
  let currentAddress = null;
  let currentPageKey = null;
  let annotations = [];
  let activeOverlay = null;
  let activeOverlayPhotoUrl = null;
  let activeOverlayContainer = null;
  let activeDropdown = null;
  let processedPhotos = new WeakSet();
  let cleanupObserver = null;
  let lastUsedLabel = '';
  const analyzingPhotoIds = new Set();

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

  function cleanup() {
    hideOverlay();
    hideDropdown();
    cleanupObserver?.();
    listingContext?.cleanup();
    window._sleepEasyListingContext = null;
    listingContext = null;
    cleanupObserver = null;
    processedPhotos = new WeakSet();
    annotations = [];
    currentListingId = null;
    analyzingPhotoIds.clear();
  }

  async function loadAnnotations() {
    if (!currentListingId) return;
    try {
      const response = await window.SleepEasyBridge.getAnnotations(currentListingId);
      if (response?.success) annotations = response.annotations || [];
    } catch {
      // Storage may not be available yet.
    }
  }

  function handleIncomingMessage(msg) {
    if (msg.type !== 'UPDATE_ANNOTATIONS' || msg.listingId !== currentListingId) return;
    annotations = msg.annotations || [];
    for (const ann of annotations) {
      if (ann.sqft !== null && ann.sqft !== undefined) analyzingPhotoIds.delete(ann.photoId);
    }
    if (activeOverlay && activeOverlayPhotoUrl && activeOverlayContainer) {
      showOverlay(activeOverlayContainer, activeOverlayPhotoUrl);
    }
  }

  function scanAndAttach() {
    if (!listingContext) return;

    const positionsToSend = {};
    for (const { element, url, position } of listingContext.getPhotoElements()) {
      if (position) positionsToSend[url] = position;
      if (processedPhotos.has(element)) continue;
      processedPhotos.add(element);
      attachPhotoListeners(element, url);
    }

    if (currentListingId && Object.keys(positionsToSend).length > 0) {
      window.SleepEasyBridge.setPhotoPositions(currentListingId, positionsToSend);
    }
  }

  function attachPhotoListeners(photoEl, photoUrl) {
    const container = listingContext.getPhotoContainer(photoEl);
    if (window.getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }

    const maybeShowOverlay = () => {
      if (activeOverlayPhotoUrl === photoUrl && activeOverlayContainer === container) return;
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

  function getPhotoAnnotation(photoUrl) {
    return annotations.find(a => a.photoUrl === photoUrl) || null;
  }

  function showOverlay(container, photoUrl) {
    hideOverlay();

    const ann = getPhotoAnnotation(photoUrl);
    const overlay = document.createElement('div');
    overlay.className = OVERLAY_CLASS;

    if (!ann) {
      overlay.innerHTML = `
        <div class="sleepsy-actions">
          <div class="sleepsy-pill">
            <button class="sleepsy-seg sleepsy-analyze-photo-btn">Analyze photo</button>
            <span class="sleepsy-div"></span>
            <button class="sleepsy-seg sleepsy-label-btn">Label room &#9662;</button>
          </div>
        </div>
      `;
      overlay.querySelector('.sleepsy-analyze-photo-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await saveAndAnalyzePhoto(photoUrl, overlay);
      });
      overlay.querySelector('.sleepsy-label-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await showLabelDropdown(container, photoUrl, null, e.currentTarget);
      });
    } else {
      const isAnalyzing = analyzingPhotoIds.has(ann.photoId);
      const hasEstimate = ann.sqft !== null && ann.sqft !== undefined;
      const labelText = ann.label ? escapeHtml(ann.label) : 'Label room';
      const sqftContent = isAnalyzing
        ? '<span class="sleepsy-spinner"></span><span class="sleepsy-loading-text">Analyzing</span>'
        : hasEstimate
          ? `<span class="sleepsy-sqft-value">${ann.sqft}</span> <span class="sleepsy-sqft-unit">sqft</span>`
          : '<span class="sleepsy-analyze-label">Analyze</span> <span class="sleepsy-sqft-unknown">???</span> <span class="sleepsy-sqft-unit">sqft</span>';

      overlay.innerHTML = `
        <div class="sleepsy-actions">
          <div class="sleepsy-pill">
            <button class="sleepsy-seg sleepsy-label-btn">${labelText} &#9662;</button>
            <span class="sleepsy-div"></span>
            <button class="sleepsy-seg sleepsy-sqft-btn"${isAnalyzing ? ' disabled' : ''}>${sqftContent}</button>
          </div>
        </div>
      `;
      overlay.querySelector('.sleepsy-label-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await showLabelDropdown(container, photoUrl, ann, e.currentTarget);
      });
      overlay.querySelector('.sleepsy-sqft-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        e.preventDefault();
        await analyzeSavedPhoto(ann.photoId, overlay);
      });
    }

    overlay.addEventListener('click', (e) => e.stopPropagation());
    container.appendChild(overlay);
    activeOverlay = overlay;
    activeOverlayPhotoUrl = photoUrl;
    activeOverlayContainer = container;
  }

  function hideOverlay() {
    activeOverlay?.remove();
    activeOverlay = null;
    activeOverlayPhotoUrl = null;
    activeOverlayContainer = null;
  }

  async function saveAndAnalyzePhoto(photoUrl, overlay) {
    const btn = overlay?.querySelector('.sleepsy-analyze-photo-btn');
    setButtonLoading(btn);
    try {
      const saved = await window.SleepEasyBridge.savePhoto(
        currentListingId,
        currentListingUrl,
        currentAddress,
        photoUrl,
        ''
      );
      if (!saved?.success || !saved.photo) throw new Error(saved?.error || 'Could not save photo');
      await analyzeSavedPhoto(saved.photo.id, overlay);
    } catch (err) {
      showToast(`Analyze failed: ${err?.message || 'Unknown error'}`);
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Analyze photo';
      }
    }
  }

  async function analyzeSavedPhoto(photoId, overlay) {
    if (!currentListingId || !photoId) return;
    analyzingPhotoIds.add(photoId);
    const btn = overlay?.querySelector('.sleepsy-sqft-btn, .sleepsy-analyze-photo-btn');
    setButtonLoading(btn);
    if (activeOverlay && activeOverlayPhotoUrl && activeOverlayContainer) {
      showOverlay(activeOverlayContainer, activeOverlayPhotoUrl);
    }

    try {
      const res = await window.SleepEasyBridge.analyzePhoto(currentListingId, photoId);
      if (!res?.success) throw new Error(res?.error || 'Unknown error');
      if (!res.deferred) analyzingPhotoIds.delete(photoId);
    } catch (err) {
      analyzingPhotoIds.delete(photoId);
      showToast(`Analyze failed: ${err?.message || 'Unknown error'}`);
    } finally {
      if (!analyzingPhotoIds.has(photoId) && activeOverlay && activeOverlayPhotoUrl && activeOverlayContainer) {
        showOverlay(activeOverlayContainer, activeOverlayPhotoUrl);
      }
    }
  }

  function setButtonLoading(btn) {
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="sleepsy-spinner"></span><span class="sleepsy-loading-text">Analyzing</span>';
  }

  async function showLabelDropdown(container, photoUrl, ann, anchorBtn) {
    hideDropdown();
    if (!currentListingId) return;

    const response = await window.SleepEasyBridge.getLabels(currentListingId);
    const labels = response?.labels || [];
    const dropdown = document.createElement('div');
    dropdown.className = DROPDOWN_CLASS;
    const currentLabel = ann?.label || '';

    dropdown.innerHTML = `
      <div class="sleepsy-dd-head">
        <div class="sleepsy-dd-title">Label room</div>
      </div>
      <div class="sleepsy-dd-list">
        <button class="sleepsy-room-pick" type="button" data-label="">No label</button>
        ${labels.map(label => `
          <button class="sleepsy-room-pick${label === currentLabel ? ' active' : ''}" type="button" data-label="${escapeHtml(label)}">
            ${escapeHtml(label)}
          </button>
        `).join('')}
      </div>
      <div class="sleepsy-dd-divider"></div>
      <div class="sleepsy-dd-create">
        <input class="sleepsy-dd-create-input" type="text" placeholder="Room label" autocomplete="off" />
        <button class="sleepsy-dd-create-btn" type="button">Save</button>
      </div>
      ${ann ? '<button class="sleepsy-dd-remove" type="button">Remove photo</button>' : ''}
    `;

    if (anchorBtn) {
      const btnRect = anchorBtn.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      dropdown.style.top = `${btnRect.bottom - containerRect.top + 4}px`;
      dropdown.style.right = `${containerRect.right - btnRect.right}px`;
    }

    async function saveLabel(label) {
      const clean = String(label || '').trim();
      lastUsedLabel = clean;
      if (ann) {
        await window.SleepEasyBridge.labelPhoto(currentListingId, ann.photoId, clean);
      } else {
        await window.SleepEasyBridge.savePhoto(
          currentListingId,
          currentListingUrl,
          currentAddress,
          photoUrl,
          clean
        );
      }
      hideDropdown();
    }

    dropdown.querySelector('.sleepsy-dd-list').addEventListener('click', async (e) => {
      const pick = e.target.closest?.('[data-label]');
      if (!pick) return;
      e.stopPropagation();
      e.preventDefault();
      await saveLabel(pick.dataset.label || '');
    });

    const input = dropdown.querySelector('.sleepsy-dd-create-input');
    const saveBtn = dropdown.querySelector('.sleepsy-dd-create-btn');
    input.value = currentLabel || lastUsedLabel;
    saveBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      await saveLabel(input.value);
    });
    input.addEventListener('keydown', async (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        await saveLabel(input.value);
      }
    });

    const removeBtn = dropdown.querySelector('.sleepsy-dd-remove');
    removeBtn?.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      await window.SleepEasyBridge.deletePhoto(currentListingId, ann.photoId);
      hideDropdown();
    });

    dropdown.addEventListener('click', e => e.stopPropagation());
    container.appendChild(dropdown);
    activeDropdown = dropdown;

    setTimeout(() => {
      document.addEventListener('click', closeDropdownOnOutsideClick, { once: true });
      input.focus();
      input.select();
    }, 0);
  }

  function hideDropdown() {
    activeDropdown?.remove();
    activeDropdown = null;
  }

  function closeDropdownOnOutsideClick(e) {
    if (activeDropdown && !activeDropdown.contains(e.target)) hideDropdown();
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

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

  function debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }
})();
