/**
 * SleepEasy Service Worker
 *
 * Responsibilities:
 * 1. Open side panel on extension icon click
 * 2. Route messages between content script <-> side panel
 * 3. Orchestrate scan/analyze requests (call API, write to storage, broadcast updates)
 *
 * MV3 service workers are ephemeral — all state lives in chrome.storage.local.
 */

importScripts('../lib/shared-types.js', '../lib/storage.js', '../lib/sqft-api.js');

const storage = new ListingStorage();
const api = new SqftEstimationAPI();

// ── Side panel: open on icon click ──

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

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

  // Notify side panel
  try {
    await chrome.runtime.sendMessage({
      type: 'STATE_CHANGED',
      target: 'sidepanel',
      listingId,
      rooms,
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

    // ── Content script: scan a single photo ──
    case 'SCAN_PHOTO': {
      (async () => {
        try {
          const { listingId, listingUrl, address, photoUrl } = message;
          const normalized = normalizePhotoUrl(photoUrl);

          // Ensure listing exists
          await storage.ensureListing(listingId, listingUrl, address);

          // Check if already scanned
          const existing = await storage.getScanForPhoto(listingId, normalized);
          if (existing) {
            sendResponse({ success: true, result: existing, cached: true });
            return;
          }

          // Run single-photo pipeline
          const estimate = await api.estimateSinglePhoto(normalized);

          // Auto-create a room for this photo
          const room = await storage.createRoom(listingId);
          await storage.addPhotoToRoom(listingId, room.id, normalized);

          // Save scan result
          const scanResult = {
            photoUrl: normalized,
            roomId: room.id,
            listingId,
            estimatedSqft: estimate.estimatedSqft,
            confidence: estimate.confidence,
            timestamp: Date.now(),
            pipeline: 'single',
            apiVersion: estimate.apiVersion,
          };
          await storage.addScanResult(scanResult);

          // Update room estimate
          await storage.updateRoomEstimate(listingId, room.id, estimate.estimatedSqft, 'single');

          // Broadcast
          await broadcastStateChange(tabId, listingId);

          sendResponse({ success: true, result: scanResult, cached: false });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true; // async response
    }

    // ── Content script: add photo to room ──
    case 'ADD_PHOTO_TO_ROOM': {
      (async () => {
        try {
          const { listingId, listingUrl, address, photoUrl, roomId } = message;
          const normalized = normalizePhotoUrl(photoUrl);

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
          const scans = await storage.getScanResults(listingId);
          sendResponse({ success: true, listing, rooms, scans });
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
            // Multi-photo pipeline
            estimate = await api.estimateMultiPhoto(room.photoUrls);
            pipeline = 'multi';
          }

          await storage.updateRoomEstimate(listingId, roomId, estimate.estimatedSqft, pipeline);

          // Save individual scan results
          for (const photoUrl of room.photoUrls) {
            const scanResult = {
              photoUrl,
              roomId,
              listingId,
              estimatedSqft: estimate.estimatedSqft,
              confidence: estimate.confidence,
              timestamp: Date.now(),
              pipeline,
              apiVersion: estimate.apiVersion,
            };
            await storage.addScanResult(scanResult);
          }

          await broadcastStateChange(tabId, listingId);

          sendResponse({
            success: true,
            result: { estimatedSqft: estimate.estimatedSqft, pipeline },
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
            const totalSqft = rooms
              .filter(r => r.estimatedSqft !== null)
              .reduce((sum, r) => sum + r.estimatedSqft, 0);
            history.push({
              ...listing,
              roomCount: rooms.length,
              totalSqft: totalSqft || null,
              analyzedRoomCount: rooms.filter(r => r.estimatedSqft !== null).length,
            });
          }
          sendResponse({ success: true, history });
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
