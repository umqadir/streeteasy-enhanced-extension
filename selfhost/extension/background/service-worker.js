/**
 * SleepEasy service worker.
 *
 * 1. Opens the side panel on extension-icon click and first photo action
 * 2. Routes messages between content script and side panel
 * 3. Orchestrates photo-level area analysis and persists results
 */

import { ListingStorage } from './storage.js';
import { SqftEstimationAPI } from './backend-api.js';

const storage = new ListingStorage();
const api = new SqftEstimationAPI();
const autoOpenedTabs = new Set();

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  autoOpenedTabs.delete(tabId);
});

async function maybeAutoOpenSidePanel(tabId) {
  if (!tabId || autoOpenedTabs.has(tabId)) return;
  autoOpenedTabs.add(tabId);
  try {
    await chrome.sidePanel.open({ tabId });
  } catch {
    // Side panel API may not be available in every window type.
  }
}

function normalizePhotoUrl(url) {
  try {
    const u = new URL(url);
    u.search = '';
    u.hash = '';
    return u.toString();
  } catch {
    return String(url || '');
  }
}

async function sendToTab(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    return null;
  }
}

async function broadcastStateChange(tabId, listingId) {
  const photos = await storage.getPhotos(listingId);
  const annotations = await storage.getAnnotations(listingId);
  const positions = await storage.getPhotoPositions(listingId);

  try {
    await chrome.runtime.sendMessage({
      type: 'STATE_CHANGED',
      target: 'sidepanel',
      listingId,
      photos,
      positions,
    });
  } catch {
    // Side panel may not be open.
  }

  const contentMessage = {
    type: 'UPDATE_ANNOTATIONS',
    target: 'content',
    listingId,
    annotations,
  };

  if (tabId) {
    await sendToTab(tabId, contentMessage);
  } else {
    const tabs = await chrome.tabs.query({});
    await Promise.all(tabs.map(tab => sendToTab(tab.id, contentMessage)));
  }
}

async function shouldUseBackend(config) {
  if (config.analysisBackend === 'inbrowser') return false;
  if (config.analysisBackend === 'local') return true;
  const health = await api.checkHealth();
  return !!health?.ok;
}

async function analyzeViaBackend(photo) {
  const estimate = await api.estimateSinglePhoto(photo.photoUrl);
  const parsedSqft = Number(estimate?.estimatedSqft ?? estimate?.estimatedSqftFloat);
  if (!Number.isFinite(parsedSqft)) throw new Error('Backend returned an invalid sqft value');
  return {
    sqft: Math.max(0, Math.round(parsedSqft)),
    pipeline: estimate?.pipeline || 'single',
    confidence: estimate?.confidence ?? null,
    method: estimate?.method ?? null,
  };
}

async function analyzePhoto(listingId, photoId) {
  const photo = await storage.getPhoto(listingId, photoId);
  if (!photo) return { success: false, error: 'Photo is not saved' };

  const config = await api.getConfig();
  if (await shouldUseBackend(config)) {
    const outcome = await analyzeViaBackend(photo);
    await storage.updatePhotoEstimate(listingId, photoId, outcome.sqft, outcome.pipeline);
    return {
      success: true,
      result: {
        estimatedSqft: outcome.sqft,
        pipeline: outcome.pipeline,
        confidence: outcome.confidence,
        method: outcome.method,
      },
    };
  }

  chrome.runtime.sendMessage({
    target: 'sidepanel',
    type: 'RUN_INBROWSER_PHOTO',
    listingId,
    photoId,
  }).catch(() => {});
  return { success: true, deferred: true };
}

async function getHistory() {
  const listings = await storage.getListings();
  const history = [];
  for (const listing of listings) {
    const photos = await storage.getPhotos(listing.id);
    const analyzedPhotoCount = photos.filter(p => p.estimatedSqft !== null && p.estimatedSqft !== undefined).length;
    history.push({
      ...listing,
      photoCount: photos.length,
      analyzedPhotoCount,
    });
  }
  return history;
}

const handlers = {
  async SAVE_PHOTO(message, tabId) {
    const { listingId, listingUrl, address, photoUrl, label } = message;
    const normalized = normalizePhotoUrl(photoUrl);

    await maybeAutoOpenSidePanel(tabId);
    await storage.ensureListing(listingId, listingUrl, address);
    const photo = await storage.savePhoto(listingId, normalized, label || '');
    await broadcastStateChange(tabId, listingId);
    return { success: true, photo };
  },

  async LABEL_PHOTO(message, tabId) {
    const { listingId, photoId, label } = message;
    await storage.labelPhoto(listingId, photoId, label || '');
    await broadcastStateChange(tabId, listingId);
    return { success: true };
  },

  async DELETE_PHOTO(message, tabId) {
    const { listingId, photoId } = message;
    await storage.deletePhoto(listingId, photoId);
    await broadcastStateChange(tabId, listingId);
    return { success: true };
  },

  async ANALYZE_PHOTO(message, tabId) {
    await maybeAutoOpenSidePanel(tabId);
    const response = await analyzePhoto(message.listingId, message.photoId);
    if (response.success) await broadcastStateChange(tabId, message.listingId);
    return response;
  },

  async GET_LABELS(message) {
    const labels = await storage.getLabels(message.listingId);
    return { success: true, labels };
  },

  async GET_LISTING_STATE(message) {
    const { listingId } = message;
    const listing = await storage.getListing(listingId);
    const photos = await storage.getPhotos(listingId);
    const positions = await storage.getPhotoPositions(listingId);
    return { success: true, listing, photos, positions };
  },

  async GET_BACKEND_CONFIG() {
    const config = await api.getConfig();
    const health = await api.checkHealth();
    return { success: true, config, health };
  },

  async SET_BACKEND_CONFIG(message) {
    const config = await api.setConfig(message.config || {});
    const health = await api.checkHealth();
    return { success: true, config, health };
  },

  async GET_BACKEND_HEALTH() {
    const health = await api.checkHealth();
    return { success: true, health };
  },

  async GET_HISTORY() {
    const history = await getHistory();
    return { success: true, history };
  },

  async SET_PHOTO_POSITIONS(message) {
    const { listingId, positions } = message;
    if (positions && typeof positions === 'object') {
      await storage.setPhotoPositions(listingId, positions);
    }
    return { success: true };
  },

  async GET_ANNOTATIONS(message) {
    const annotations = await storage.getAnnotations(message.listingId);
    return { success: true, annotations };
  },

  async SET_PHOTO_ESTIMATE(message, tabId) {
    const { listingId, photoId, sqft, pipeline } = message;
    const parsed = Math.max(0, Math.round(Number(sqft) || 0));
    await storage.updatePhotoEstimate(listingId, photoId, parsed, pipeline || 'inbrowser');
    await broadcastStateChange(tabId, listingId);
    return { success: true };
  },
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handler = handlers[message?.type];
  if (!handler) return false;

  const tabId = sender.tab?.id || null;
  handler(message, tabId)
    .then(sendResponse)
    .catch(err => sendResponse({ success: false, error: err?.message || String(err) }));
  return true;
});
