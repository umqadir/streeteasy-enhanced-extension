/**
 * StreetSafe Service Worker
 * Handles background tasks, API calls, and side panel management
 */

'use strict';

// Store current location data for side panel access
let currentLocationData = null;

/**
 * Handle extension installation
 */
chrome.runtime.onInstalled.addListener((details) => {
  console.log('[StreetSafe] Extension installed:', details.reason);

  if (details.reason === 'install') {
    // Set default settings
    chrome.storage.local.set({
      settings: {
        defaultTimeWindow: '12m',
        autoOpenSidePanel: false
      }
    });
  }
});

/**
 * Handle messages from content scripts
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[StreetSafe Service Worker] Received message:', message);

  switch (message.type) {
    case 'LOCATION_UPDATED':
      handleLocationUpdate(message.data);
      break;

    case 'OPEN_SIDE_PANEL':
      handleOpenSidePanel(sender.tab?.id);
      break;

    case 'CHANGE_TIME_WINDOW':
      handleTimeWindowChange(message.window, sender.tab?.id);
      break;

    case 'GET_CURRENT_DATA':
      sendResponse({ data: currentLocationData });
      break;
  }

  return true; // Keep message channel open for async responses
});

/**
 * Handle location update from content script
 * @param {Object} data - Location and stats data
 */
function handleLocationUpdate(data) {
  console.log('[StreetSafe Service Worker] Location updated:', data);
  currentLocationData = data;

  // Broadcast to side panel if open
  chrome.runtime.sendMessage({
    type: 'DATA_UPDATED',
    data: data
  }).catch(() => {
    // Side panel might not be open, ignore error
  });
}

/**
 * Handle side panel open request
 * @param {number} tabId - Tab ID requesting side panel
 */
async function handleOpenSidePanel(tabId) {
  try {
    if (tabId) {
      await chrome.sidePanel.open({ tabId });
      console.log('[StreetSafe Service Worker] Side panel opened');
    }
  } catch (error) {
    console.error('[StreetSafe Service Worker] Error opening side panel:', error);
  }
}

/**
 * Handle time window change request
 * @param {string} window - New time window
 * @param {number} tabId - Tab ID
 */
function handleTimeWindowChange(window, tabId) {
  console.log('[StreetSafe Service Worker] Time window changed to:', window);

  // Send message back to content script to refetch data
  if (tabId) {
    chrome.tabs.sendMessage(tabId, {
      type: 'CHANGE_TIME_WINDOW',
      window: window
    }).catch((error) => {
      console.error('[StreetSafe Service Worker] Error sending message to tab:', error);
    });
  }
}

/**
 * Handle extension icon click
 */
chrome.action.onClicked.addListener(async (tab) => {
  console.log('[StreetSafe Service Worker] Extension icon clicked');

  // Open side panel
  try {
    await chrome.sidePanel.open({ tabId: tab.id });
  } catch (error) {
    console.error('[StreetSafe Service Worker] Error opening side panel:', error);
  }
});

/**
 * Clean up when tab is closed
 */
chrome.tabs.onRemoved.addListener((tabId) => {
  console.log('[StreetSafe Service Worker] Tab closed:', tabId);
  // Could clean up tab-specific data here if needed
});

console.log('[StreetSafe] Service worker initialized');
