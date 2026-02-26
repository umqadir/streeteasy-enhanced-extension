/**
 * SleepEasy Area Analysis - Shared Type Definitions
 *
 * JSDoc types used across content scripts, service worker, and side panel.
 * No runtime code — import for IDE support only.
 */

/**
 * @typedef {Object} Listing
 * @property {string} id        - Stable identifier derived from URL (e.g., "rental:12345")
 * @property {string} url       - Canonical StreetEasy URL
 * @property {string|null} address - Extracted address text
 * @property {number} createdAt - Epoch ms when first analyzed
 * @property {number} updatedAt - Epoch ms of last modification
 */

/**
 * @typedef {Object} Room
 * @property {string} id         - UUID (crypto.randomUUID())
 * @property {string} listingId  - Parent listing ID
 * @property {string} name       - User-editable name (default: "Room 1", "Room 2", ...)
 * @property {string[]} photoUrls - Normalized CDN paths assigned to this room
 * @property {number|null} estimatedSqft - Latest sqft estimate (null = not yet analyzed)
 * @property {string|null} pipeline      - "single" | "multi" (which pipeline produced the estimate)
 * @property {number|null} analyzedAt    - Epoch ms of last analysis
 * @property {boolean} outdated          - True if photos changed since last analysis
 */

/**
 * @typedef {Object} PhotoAnnotation
 * @property {string} photoUrl  - Normalized CDN path
 * @property {string} roomId    - Room this photo belongs to
 * @property {string} roomName  - Display name of the room
 * @property {number|null} sqft - Sqft estimate for the room (null if not analyzed)
 * @property {number} photoCount - Total photos in this room
 * @property {string|null} pipeline - "single" | "multi" (which pipeline produced sqft)
 * @property {boolean} outdated - True if photos changed since last analysis
 * @property {number|null} position - Carousel position number for this photo (if known)
 */

// Message types used in chrome.runtime messaging

/**
 * @typedef {Object} SleepEasyMessage
 * @property {string} type   - Message type identifier
 * @property {string} [target] - Routing target: 'background' | 'content' | 'sidepanel'
 */
