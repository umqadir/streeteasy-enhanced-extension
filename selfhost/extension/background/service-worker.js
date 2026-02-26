/**
 * SleepEasy Service Worker
 *
 * Responsibilities:
 * 1. Open side panel on extension icon click
 * 2. Route messages between content script <-> side panel
 * 3. Orchestrate room area analysis requests (call API, write to storage, broadcast updates)
 *
 * MV3 service workers are ephemeral — all state lives in chrome.storage.local.
 */

// MV3 service workers cannot use importScripts after initial evaluation.
// Instead, we inline the dependencies or use ES modules.
// For now, duplicate the minimal needed code inline.

// ── Inline: ListingStorage ──

class ListingStorage {
  async _get(key) {
    const result = await chrome.storage.local.get(key);
    return result[key] ?? null;
  }
  async _set(key, value) {
    await chrome.storage.local.set({ [key]: value });
  }
  async _remove(key) {
    await chrome.storage.local.remove(key);
  }
  _roomsKey(id) { return `area:rooms:${id}`; }

  async getListingsIndex() {
    return (await this._get('area:listings')) || {};
  }
  async getListings() {
    const idx = await this.getListingsIndex();
    return Object.values(idx).sort((a, b) => b.updatedAt - a.updatedAt);
  }
  async getListing(listingId) {
    const idx = await this.getListingsIndex();
    return idx[listingId] || null;
  }
  async saveListing(listing) {
    const idx = await this.getListingsIndex();
    idx[listing.id] = listing;
    await this._set('area:listings', idx);
  }
  async ensureListing(listingId, url, address) {
    let listing = await this.getListing(listingId);
    if (!listing) {
      listing = { id: listingId, url: url || '', address: address || null, createdAt: Date.now(), updatedAt: Date.now() };
      await this.saveListing(listing);
    }
    return listing;
  }
  async deleteListing(listingId) {
    const idx = await this.getListingsIndex();
    delete idx[listingId];
    await this._set('area:listings', idx);
    await this._remove(this._roomsKey(listingId));
  }
  async getRooms(listingId) {
    return (await this._get(this._roomsKey(listingId))) || [];
  }
  async getRoom(listingId, roomId) {
    const rooms = await this.getRooms(listingId);
    return rooms.find(r => r.id === roomId) || null;
  }
  async _saveRooms(listingId, rooms) {
    await this._set(this._roomsKey(listingId), rooms);
    const listing = await this.getListing(listingId);
    if (listing) { listing.updatedAt = Date.now(); await this.saveListing(listing); }
  }
  async createRoom(listingId, name) {
    const rooms = await this.getRooms(listingId);
    const room = {
      id: crypto.randomUUID(), listingId, name: name || `Room ${rooms.length + 1}`,
      photoUrls: [], estimatedSqft: null, pipeline: null, analyzedAt: null, outdated: false,
    };
    rooms.push(room);
    await this._saveRooms(listingId, rooms);
    return room;
  }

  /**
   * Any mutation to a room's photo set invalidates its area estimate.
   * Product requirement: no stale sqft after adds/removes/moves.
   * @param {Room} room
   */
  _invalidateRoomEstimate(room) {
    room.estimatedSqft = null;
    room.pipeline = null;
    room.analyzedAt = null;
    room.outdated = false;
  }
  async updateRoom(listingId, roomId, updates) {
    const rooms = await this.getRooms(listingId);
    const idx = rooms.findIndex(r => r.id === roomId);
    if (idx === -1) return null;
    Object.assign(rooms[idx], updates);
    await this._saveRooms(listingId, rooms);
    return rooms[idx];
  }
  async deleteRoom(listingId, roomId) {
    let rooms = await this.getRooms(listingId);
    rooms = rooms.filter(r => r.id !== roomId);
    await this._saveRooms(listingId, rooms);
  }
  async addPhotoToRoom(listingId, roomId, photoUrl) {
    const rooms = await this.getRooms(listingId);
    const room = rooms.find(r => r.id === roomId);
    if (!room || room.photoUrls.includes(photoUrl)) return;
    room.photoUrls.push(photoUrl);
    this._invalidateRoomEstimate(room);
    await this._saveRooms(listingId, rooms);
  }
  async removePhotoFromRoom(listingId, roomId, photoUrl) {
    const rooms = await this.getRooms(listingId);
    const room = rooms.find(r => r.id === roomId);
    if (!room) return;
    room.photoUrls = room.photoUrls.filter(u => u !== photoUrl);
    this._invalidateRoomEstimate(room);
    await this._saveRooms(listingId, rooms);
  }
  async findRoomForPhoto(listingId, photoUrl) {
    const rooms = await this.getRooms(listingId);
    return rooms.find(r => r.photoUrls.includes(photoUrl)) || null;
  }
  async updateRoomEstimate(listingId, roomId, sqft, pipeline) {
    await this.updateRoom(listingId, roomId, { estimatedSqft: sqft, pipeline, analyzedAt: Date.now(), outdated: false });
  }
  async getAnnotations(listingId) {
    const rooms = await this.getRooms(listingId);
    const positions = await this._getPhotoPositions(listingId);

    const annotations = [];
    for (const room of rooms) {
      for (const photoUrl of room.photoUrls) {
        annotations.push({
          photoUrl, roomId: room.id, roomName: room.name,
          sqft: room.estimatedSqft, photoCount: room.photoUrls.length,
          pipeline: room.pipeline || null, outdated: !!room.outdated,
          position: positions[photoUrl] || null,
        });
      }
    }
    return annotations;
  }

  // Photo position tracking (carousel number -> photoUrl)
  _positionsKey(id) { return `area:positions:${id}`; }
  async _getPhotoPositions(listingId) {
    return (await this._get(this._positionsKey(listingId))) || {};
  }
  async setPhotoPosition(listingId, photoUrl, position) {
    const positions = await this._getPhotoPositions(listingId);
    positions[photoUrl] = position;
    await this._set(this._positionsKey(listingId), positions);
  }
  async setPhotoPositions(listingId, positionsMap) {
    const existing = await this._getPhotoPositions(listingId);
    Object.assign(existing, positionsMap);
    await this._set(this._positionsKey(listingId), existing);
  }
}

// ── Inline: SqftEstimationAPI ──

const BACKEND_CONFIG_KEY = 'area:backend:config';
const DEFAULT_BACKEND_CONFIG = Object.freeze({
  baseUrl: 'http://127.0.0.1:8787',
  devicePolicy: 'auto', // auto | cpu | mps
  analysisMode: 'auto', // auto | single-image
  noCudaPromptHandled: false,
});

class SqftEstimationAPI {
  constructor(options = {}) {
    this.apiVersion = 'v2-local-client';
    this.config = this._sanitizeConfig({ ...DEFAULT_BACKEND_CONFIG, ...options });
    this._ready = this._loadConfig();
  }

  async _loadConfig() {
    try {
      const result = await chrome.storage.local.get(BACKEND_CONFIG_KEY);
      const stored = result?.[BACKEND_CONFIG_KEY] || {};
      this.config = this._sanitizeConfig({ ...this.config, ...stored });
    } catch {
      this.config = this._sanitizeConfig(this.config);
    }
  }

  _sanitizeConfig(config = {}) {
    const rawUrl = String(config.baseUrl || DEFAULT_BACKEND_CONFIG.baseUrl).trim();
    const baseUrl = rawUrl.replace(/\/+$/, '');
    const devicePolicy = ['auto', 'cpu', 'mps'].includes(String(config.devicePolicy))
      ? String(config.devicePolicy)
      : 'auto';
    const analysisMode = ['auto', 'single-image'].includes(String(config.analysisMode))
      ? String(config.analysisMode)
      : 'auto';
    return {
      baseUrl,
      devicePolicy,
      analysisMode,
      noCudaPromptHandled: config.noCudaPromptHandled === true,
    };
  }

  async ready() {
    await this._ready;
  }

  async getConfig() {
    await this.ready();
    return { ...this.config };
  }

  async setConfig(nextConfig = {}) {
    await this.ready();
    this.config = this._sanitizeConfig({ ...this.config, ...nextConfig });
    await chrome.storage.local.set({ [BACKEND_CONFIG_KEY]: this.config });
    await this._pushBackendConfig();
    return { ...this.config };
  }

  async _pushBackendConfig() {
    await this._fetchJson('/backend/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        devicePolicy: this.config.devicePolicy,
        analysisMode: this.config.analysisMode,
      }),
    }, 15000);
  }

  async _fetchJson(path, options = {}, timeoutMs = 300000) {
    const baseUrl = this.config.baseUrl;
    if (!baseUrl) {
      throw new Error('Backend URL is empty');
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        ...options,
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Backend ${res.status}: ${body || res.statusText}`);
      }
      return await res.json();
    } catch (err) {
      if (err?.name === 'AbortError') {
        throw new Error(`Backend request timeout (${timeoutMs}ms): ${path}`);
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  async checkHealth() {
    await this.ready();
    try {
      return await this._fetchJson('/health', {}, 10000);
    } catch (err) {
      return {
        ok: false,
        error: err?.message || 'Backend unavailable',
      };
    }
  }

  async estimateSinglePhoto(imageUrl) {
    await this.ready();
    await this._pushBackendConfig();
    return await this._fetchJson('/estimate/single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        imageUrl,
        devicePolicy: this.config.devicePolicy,
      }),
    });
  }

  async estimateMultiPhoto(imageUrls, options = {}) {
    await this.ready();
    const requested = String(options.multiviewMethod || '').trim().toLowerCase();
    const effectiveMethod = (imageUrls.length <= 1)
      ? 'single-image'
      : (requested || (this.config.analysisMode === 'single-image' ? 'single-image' : 'dust3r-scene'));
    if (!['dust3r-scene', 'single-image'].includes(effectiveMethod)) {
      throw new Error(`Unsupported multiview method: ${effectiveMethod}`);
    }
    await this._pushBackendConfig();
    return await this._fetchJson('/estimate/multi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        imageUrls,
        devicePolicy: this.config.devicePolicy,
        multiviewMethod: effectiveMethod,
      }),
    });
  }
}

const storage = new ListingStorage();
const api = new SqftEstimationAPI();

// Track which tabs have had the side panel auto-opened to avoid repeats.
const autoOpenedTabs = new Set();

// ── Side panel: open on icon click ──

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

// Clean up tab tracking when tabs close
chrome.tabs.onRemoved.addListener((tabId) => {
  autoOpenedTabs.delete(tabId);
});

/**
 * Auto-open the side panel on first sqft interaction for a tab.
 */
async function maybeAutoOpenSidePanel(tabId) {
  if (!tabId || autoOpenedTabs.has(tabId)) return;
  autoOpenedTabs.add(tabId);
  try {
    await chrome.sidePanel.open({ tabId });
  } catch {
    // Side panel API may not be available
  }
}

// ── Utility: normalize photo URL for stable storage keys ──

function normalizePhotoUrl(url) {
  try {
    const u = new URL(url);
    u.search = '';
    u.hash = '';
    return u.toString();
  } catch {
    return url;
  }
}

// ── Utility: send message to a specific tab's content script ──

async function sendToTab(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    // Tab may not have the content script loaded
    return null;
  }
}

// ── Utility: broadcast update to side panel and content script ──

async function broadcastStateChange(tabId, listingId) {
  const rooms = await storage.getRooms(listingId);
  const annotations = await storage.getAnnotations(listingId);
  const positions = await storage._getPhotoPositions(listingId);

  // Notify side panel
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

  // Notify content script
  if (tabId) {
    await sendToTab(tabId, {
      type: 'UPDATE_ANNOTATIONS',
      target: 'content',
      listingId,
      annotations,
    });
  }
}

// ── Message handler ──

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id || null;

  switch (message.type) {

    // ── Content script: add photo to room ──
    case 'ADD_PHOTO_TO_ROOM': {
      (async () => {
        try {
          const { listingId, listingUrl, address, photoUrl, roomId } = message;
          const normalized = normalizePhotoUrl(photoUrl);

          maybeAutoOpenSidePanel(tabId);
          await storage.ensureListing(listingId, listingUrl, address);

          // Remove from any existing room first
          const existingRoom = await storage.findRoomForPhoto(listingId, normalized);
          if (existingRoom) {
            await storage.removePhotoFromRoom(listingId, existingRoom.id, normalized);
          }

          await storage.addPhotoToRoom(listingId, roomId, normalized);
          await broadcastStateChange(tabId, listingId);

          sendResponse({ success: true });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Content script: remove photo from room ──
    case 'REMOVE_PHOTO_FROM_ROOM': {
      (async () => {
        try {
          const { listingId, photoUrl, roomId } = message;
          const normalized = normalizePhotoUrl(photoUrl);

          await storage.removePhotoFromRoom(listingId, roomId, normalized);
          await broadcastStateChange(tabId, listingId);

          sendResponse({ success: true });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Create a new room ──
    case 'CREATE_ROOM': {
      (async () => {
        try {
          const { listingId, listingUrl, address, name } = message;
          await storage.ensureListing(listingId, listingUrl, address);
          const room = await storage.createRoom(listingId, name);
          await broadcastStateChange(tabId, listingId);
          sendResponse({ success: true, room });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Get rooms for a listing ──
    case 'GET_ROOMS': {
      (async () => {
        try {
          const rooms = await storage.getRooms(message.listingId);
          sendResponse({ success: true, rooms });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: get full listing state ──
    case 'GET_LISTING_STATE': {
      (async () => {
        try {
          const { listingId } = message;
          const listing = await storage.getListing(listingId);
          const rooms = await storage.getRooms(listingId);
          const positions = await storage._getPhotoPositions(listingId);
          sendResponse({ success: true, listing, rooms, positions });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: backend settings + health ──
    case 'GET_BACKEND_CONFIG': {
      (async () => {
        try {
          const config = await api.getConfig();
          const health = await api.checkHealth();
          sendResponse({ success: true, config, health });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    case 'SET_BACKEND_CONFIG': {
      (async () => {
        try {
          const config = await api.setConfig(message.config || {});
          const health = await api.checkHealth();
          sendResponse({ success: true, config, health });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    case 'GET_BACKEND_HEALTH': {
      (async () => {
        try {
          const health = await api.checkHealth();
          sendResponse({ success: true, health });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: analyze a room (multi-photo or single) ──
    case 'ANALYZE_ROOM': {
      (async () => {
        try {
          const { listingId, roomId } = message;
          const room = await storage.getRoom(listingId, roomId);
          if (!room || room.photoUrls.length === 0) {
            sendResponse({ success: false, error: 'Room has no photos' });
            return;
          }

          let estimate;
          let pipeline;

          if (room.photoUrls.length === 1) {
            // Single-photo pipeline
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
              sendResponse({
                success: false,
                errorCode: 'NO_CUDA_MULTI_UNAVAILABLE',
                promptRequired: !config.noCudaPromptHandled,
                error: 'Multi-photo DUSt3R requires CUDA on this release. Enable single-image mode in settings.',
                details: {
                  cudaAvailable: false,
                  analysisMode: config.analysisMode,
                },
              });
              return;
            }

            const multiviewMethod = (config.analysisMode === 'single-image') ? 'single-image' : 'dust3r-scene';
            // Multi-photo pipeline
            estimate = await api.estimateMultiPhoto(room.photoUrls, { multiviewMethod });
            pipeline = multiviewMethod === 'single-image' ? 'single' : 'multi';
            if (estimate?.pipeline === 'single' || estimate?.pipeline === 'multi') {
              pipeline = estimate.pipeline;
            }
          }

          const parsedSqft = Number(estimate?.estimatedSqft ?? estimate?.estimatedSqftFloat);
          if (!Number.isFinite(parsedSqft)) {
            throw new Error('Backend returned invalid sqft value');
          }
          const sqft = Math.max(0, Math.round(parsedSqft));

          await storage.updateRoomEstimate(listingId, roomId, sqft, pipeline);

          await broadcastStateChange(tabId, listingId);

          sendResponse({
            success: true,
            result: {
              estimatedSqft: sqft,
              pipeline,
              confidence: estimate?.confidence ?? null,
              method: estimate?.method ?? null,
            },
          });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: rename room ──
    case 'RENAME_ROOM': {
      (async () => {
        try {
          const { listingId, roomId, name } = message;
          await storage.updateRoom(listingId, roomId, { name });
          await broadcastStateChange(tabId, listingId);
          sendResponse({ success: true });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: delete room ──
    case 'DELETE_ROOM': {
      (async () => {
        try {
          const { listingId, roomId } = message;
          await storage.deleteRoom(listingId, roomId);
          await broadcastStateChange(tabId, listingId);
          sendResponse({ success: true });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: get history ──
    case 'GET_HISTORY': {
      (async () => {
        try {
          const listings = await storage.getListings();
          // For each listing, get room count and total sqft
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
          sendResponse({ success: true, history });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Content script: set photo positions (carousel numbers) ──
    case 'SET_PHOTO_POSITIONS': {
      (async () => {
        try {
          const { listingId, positions } = message;
          if (positions && typeof positions === 'object') {
            await storage.setPhotoPositions(listingId, positions);
          }
          sendResponse({ success: true });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }

    // ── Side panel: get annotations for current listing (for content script sync) ──
    case 'GET_ANNOTATIONS': {
      (async () => {
        try {
          const annotations = await storage.getAnnotations(message.listingId);
          sendResponse({ success: true, annotations });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true;
    }
  }
});
