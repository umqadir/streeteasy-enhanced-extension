/**
 * SleepEasy photo-analysis content bridge.
 */

(function () {
  'use strict';

  const ContentBridge = {
    async send(message) {
      return chrome.runtime.sendMessage(message);
    },

    async savePhoto(listingId, listingUrl, address, photoUrl, label = '') {
      return this.send({
        type: 'SAVE_PHOTO',
        listingId,
        listingUrl,
        address,
        photoUrl,
        label,
      });
    },

    async labelPhoto(listingId, photoId, label) {
      return this.send({ type: 'LABEL_PHOTO', listingId, photoId, label });
    },

    async deletePhoto(listingId, photoId) {
      return this.send({ type: 'DELETE_PHOTO', listingId, photoId });
    },

    async analyzePhoto(listingId, photoId) {
      return this.send({ type: 'ANALYZE_PHOTO', listingId, photoId });
    },

    async getLabels(listingId) {
      return this.send({ type: 'GET_LABELS', listingId });
    },

    async getAnnotations(listingId) {
      return this.send({ type: 'GET_ANNOTATIONS', listingId });
    },

    async setPhotoPositions(listingId, positions) {
      return this.send({ type: 'SET_PHOTO_POSITIONS', listingId, positions });
    },

    onMessage(handler) {
      chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
        if (msg.target !== 'content') return;

        if (msg.type === 'GET_LISTING_CONTEXT') {
          const ctx = window._sleepEasyListingContext;
          sendResponse(ctx ? {
            listingId: ctx.getListingId(),
            address: ctx.getAddress(),
            listingUrl: ctx.getListingUrl(),
          } : null);
          return;
        }

        handler(msg);
      });
    },
  };

  window.SleepEasyBridge = ContentBridge;
})();
