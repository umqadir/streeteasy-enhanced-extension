/**
 * SleepEasy Area Analysis - Content Script Bridge
 *
 * Thin wrapper around chrome.runtime messaging for typed communication
 * between content script <-> service worker.
 */

(function () {
  'use strict';

  const ContentBridge = {
    /**
     * Send a message to the service worker and await a response.
     * @param {Object} message
     * @returns {Promise<any>}
     */
    async send(message) {
      return chrome.runtime.sendMessage(message);
    },

    /**
     * Add a photo to an existing room.
     * @param {string} listingId
     * @param {string} listingUrl
     * @param {string|null} address
     * @param {string} photoUrl
     * @param {string} roomId
     * @returns {Promise<{success: boolean}>}
     */
    async addPhotoToRoom(listingId, listingUrl, address, photoUrl, roomId) {
      return this.send({
        type: 'ADD_PHOTO_TO_ROOM',
        listingId,
        listingUrl,
        address,
        photoUrl,
        roomId,
      });
    },

    /**
     * Remove a photo from a room.
     * @param {string} listingId
     * @param {string} photoUrl
     * @param {string} roomId
     * @returns {Promise<{success: boolean}>}
     */
    async removePhotoFromRoom(listingId, photoUrl, roomId) {
      return this.send({
        type: 'REMOVE_PHOTO_FROM_ROOM',
        listingId,
        photoUrl,
        roomId,
      });
    },

    /**
     * Create a new room.
     * @param {string} listingId
     * @param {string} listingUrl
     * @param {string|null} address
     * @param {string} [name]
     * @returns {Promise<{success: boolean, room?: Room}>}
     */
    async createRoom(listingId, listingUrl, address, name) {
      return this.send({
        type: 'CREATE_ROOM',
        listingId,
        listingUrl,
        address,
        name,
      });
    },

    /**
     * Get all rooms for a listing.
     * @param {string} listingId
     * @returns {Promise<{success: boolean, rooms?: Room[]}>}
     */
    async getRooms(listingId) {
      return this.send({ type: 'GET_ROOMS', listingId });
    },

    /**
     * Get annotations for all photos in a listing.
     * @param {string} listingId
     * @returns {Promise<{success: boolean, annotations?: PhotoAnnotation[]}>}
     */
    async getAnnotations(listingId) {
      return this.send({ type: 'GET_ANNOTATIONS', listingId });
    },

    /**
     * Estimate a room (pipeline determined by photo count in the service worker).
     * @param {string} listingId
     * @param {string} roomId
     * @returns {Promise<{success: boolean, result?: {estimatedSqft: number, pipeline: string}, error?: string}>}
     */
    async analyzeRoom(listingId, roomId) {
      return this.send({ type: 'ANALYZE_ROOM', listingId, roomId });
    },

    /**
     * Rename a room (used by overlay and side panel).
     * @param {string} listingId
     * @param {string} roomId
     * @param {string} name
     * @returns {Promise<{success: boolean, error?: string}>}
     */
    async renameRoom(listingId, roomId, name) {
      return this.send({ type: 'RENAME_ROOM', listingId, roomId, name });
    },

    /**
     * Send photo position data (carousel numbers) to the service worker.
     * @param {string} listingId
     * @param {Object<string, number>} positions - Map of photoUrl -> carousel position
     * @returns {Promise<{success: boolean}>}
     */
    async setPhotoPositions(listingId, positions) {
      return this.send({ type: 'SET_PHOTO_POSITIONS', listingId, positions });
    },

    /**
     * Listen for incoming messages from the service worker / side panel.
     * @param {function} handler - Receives (message) when target === 'content'
     */
    onMessage(handler) {
      chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
        if (msg.target !== 'content') return;

        // Respond to GET_LISTING_CONTEXT from side panel (needs sendResponse)
        if (msg.type === 'GET_LISTING_CONTEXT') {
          const ctx = window._sleepEasyListingContext;
          if (ctx) {
            sendResponse({
              listingId: ctx.getListingId(),
              address: ctx.getAddress(),
              listingUrl: ctx.getListingUrl(),
            });
          } else {
            sendResponse(null);
          }
          return; // synchronous response, no need for return true
        }

        // All other content-targeted messages
        handler(msg);
      });
    },
  };

  window.SleepEasyBridge = ContentBridge;
})();
