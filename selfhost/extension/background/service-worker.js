/**
 * SleepEasy service worker.
 *
 * 1. Opens the side panel on extension-icon click (and on first sqft action per tab)
 * 2. Routes messages between content script <-> side panel
 * 3. Orchestrates room area analysis: calls the local backend, persists results,
 *    broadcasts state changes
 *
 * MV3 service workers are ephemeral — all state lives in chrome.storage.local
 * via ListingStorage.
 */

import { ListingStorage } from './storage.js';
import { SqftEstimationAPI } from './backend-api.js';

const storage = new ListingStorage();
const api = new SqftEstimationAPI();

// Tabs that already had the side panel auto-opened (avoid repeats).
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
    // Side panel API may not be available (e.g., popup windows)
  }
}

/**
 * Photo URLs are storage keys; the same normalization must be applied on
 * every write path. (listing-context.js applies the same rule on read.)
 */
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
    // Tab may not have the content script loaded
    return null;
  }
}

async function broadcastStateChange(tabId, listingId) {
  const rooms = await storage.getRooms(listingId);
  const annotations = await storage.getAnnotations(listingId);
  const positions = await storage.getPhotoPositions(listingId);

  try {
    await chrome.runtime.sendMessage({
      type: 'STATE_CHANGED',
      target: 'sidepanel',
      listingId,
      rooms,
      positions,
    });
  } catch {
    // Side panel may not be open
  }

  if (tabId) {
    await sendToTab(tabId, {
      type: 'UPDATE_ANNOTATIONS',
      target: 'content',
      listingId,
      annotations,
    });
  }
}

/**
 * Estimate a room's floor area via the local backend.
 * Returns the shape the UI layers expect, or throws.
 */
async function analyzeRoom(listingId, roomId) {
  const room = await storage.getRoom(listingId, roomId);
  if (!room || room.photoUrls.length === 0) {
    return { success: false, error: 'Room has no photos' };
  }

  let estimate;
  let pipeline;

  if (room.photoUrls.length === 1) {
    estimate = await api.estimateSinglePhoto(room.photoUrls[0]);
    pipeline = 'single';
  } else {
    const config = await api.getConfig();
    const health = await api.checkHealth();
    const noCudaMultiBlocked = (
      !!health?.ok
      && config.analysisMode !== 'single-image'
      && health?.capabilities?.cudaAvailable === false
    );
    if (noCudaMultiBlocked) {
      return {
        success: false,
        errorCode: 'NO_CUDA_MULTI_UNAVAILABLE',
        promptRequired: !config.noCudaPromptHandled,
        error: 'Multi-photo DUSt3R requires CUDA on this release. Enable single-image mode in settings.',
        details: { cudaAvailable: false, analysisMode: config.analysisMode },
      };
    }

    const multiviewMethod = (config.analysisMode === 'single-image') ? 'single-image' : 'dust3r-scene';
    estimate = await api.estimateMultiPhoto(room.photoUrls, { multiviewMethod });
    pipeline = multiviewMethod === 'single-image' ? 'single' : 'multi';
    if (estimate?.pipeline === 'single' || estimate?.pipeline === 'multi') {
      pipeline = estimate.pipeline;
    }
  }

  const parsedSqft = Number(estimate?.estimatedSqft ?? estimate?.estimatedSqftFloat);
  if (!Number.isFinite(parsedSqft)) {
    throw new Error('Backend returned an invalid sqft value');
  }
  const sqft = Math.max(0, Math.round(parsedSqft));

  await storage.updateRoomEstimate(listingId, roomId, sqft, pipeline);

  return {
    success: true,
    result: {
      estimatedSqft: sqft,
      pipeline,
      confidence: estimate?.confidence ?? null,
      method: estimate?.method ?? null,
    },
  };
}

/**
 * Build the per-listing history summary shown in the side panel accordion.
 * A room's estimate only counts toward the listing total when it is current
 * (not outdated) and was produced by the appropriate pipeline for its photo count.
 */
async function getHistory() {
  const listings = await storage.getListings();
  const history = [];
  for (const listing of listings) {
    const rooms = await storage.getRooms(listing.id);
    const validRooms = rooms.filter(r => {
      if (r.estimatedSqft === null || r.estimatedSqft === undefined) return false;
      if (r.outdated) return false;
      if (r.photoUrls.length > 1 && r.pipeline === 'single') return false;
      return true;
    });
    const totalSqft = validRooms.reduce((sum, r) => sum + r.estimatedSqft, 0);
    history.push({
      ...listing,
      roomCount: rooms.length,
      // Preserve legitimate 0 values instead of coercing to null via truthiness.
      totalSqft: validRooms.length > 0 ? totalSqft : null,
      analyzedRoomCount: validRooms.length,
    });
  }
  return history;
}

// ── Message handler ──
//
// Every handler resolves to a {success, ...} payload; thrown errors become
// {success: false, error}. `return true` keeps the message channel open for
// the async response.

const handlers = {
  async ADD_PHOTO_TO_ROOM(message, tabId) {
    const { listingId, listingUrl, address, photoUrl, roomId } = message;
    const normalized = normalizePhotoUrl(photoUrl);

    maybeAutoOpenSidePanel(tabId);
    await storage.ensureListing(listingId, listingUrl, address);

    // A photo belongs to at most one room: remove from any existing room first.
    const existingRoom = await storage.findRoomForPhoto(listingId, normalized);
    if (existingRoom) {
      await storage.removePhotoFromRoom(listingId, existingRoom.id, normalized);
    }

    await storage.addPhotoToRoom(listingId, roomId, normalized);
    await broadcastStateChange(tabId, listingId);
    return { success: true };
  },

  async REMOVE_PHOTO_FROM_ROOM(message, tabId) {
    const { listingId, photoUrl, roomId } = message;
    await storage.removePhotoFromRoom(listingId, roomId, normalizePhotoUrl(photoUrl));
    await broadcastStateChange(tabId, listingId);
    return { success: true };
  },

  async CREATE_ROOM(message, tabId) {
    const { listingId, listingUrl, address, name } = message;
    await storage.ensureListing(listingId, listingUrl, address);
    const room = await storage.createRoom(listingId, name);
    await broadcastStateChange(tabId, listingId);
    return { success: true, room };
  },

  async GET_ROOMS(message) {
    const rooms = await storage.getRooms(message.listingId);
    return { success: true, rooms };
  },

  async GET_LISTING_STATE(message) {
    const { listingId } = message;
    const listing = await storage.getListing(listingId);
    const rooms = await storage.getRooms(listingId);
    const positions = await storage.getPhotoPositions(listingId);
    return { success: true, listing, rooms, positions };
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

  async ANALYZE_ROOM(message, tabId) {
    const response = await analyzeRoom(message.listingId, message.roomId);
    if (response.success) {
      await broadcastStateChange(tabId, message.listingId);
    }
    return response;
  },

  async RENAME_ROOM(message, tabId) {
    const { listingId, roomId, name } = message;
    await storage.renameRoom(listingId, roomId, name);
    await broadcastStateChange(tabId, listingId);
    return { success: true };
  },

  async DELETE_ROOM(message, tabId) {
    const { listingId, roomId } = message;
    await storage.deleteRoom(listingId, roomId);
    await broadcastStateChange(tabId, listingId);
    return { success: true };
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
