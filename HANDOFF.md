# SleepEasy Chrome Extension - Engineering Handoff

## Part 1: Project Context & Problem Background

### What Is This

SleepEasy is a Chrome extension for StreetEasy (NYC apartment listing site). It has two independent modules that inject UI into listing pages:

1. **Crime statistics module** (complete, working, do not touch): Injects an inline widget showing neighborhood crime risk data below the listing details. Uses local JSON data files, coordinate extraction, and shadow DOM injection.

2. **Area analysis / sqft estimation module** (in progress, your work): Lets users scan listing photos to estimate visible floor area in sqft. Users assign photos to "rooms" (like Spotify playlists), then the extension estimates per-photo and per-room sqft. This module uses a hover overlay on the photo carousel, a Chrome side panel for room management, and a service worker for state/storage.

### Architecture Overview

```
extension/
  manifest.json                          # MV3 manifest
  background/
    service-worker.js                    # Central state manager, API calls, message routing
  content/
    navigation-hook.js                   # MAIN world script - intercepts history.pushState
    main.js                              # Crime module orchestrator (DO NOT MODIFY)
    coordinates-extractor.js             # Crime module (DO NOT MODIFY)
    ui-injector.js                       # Crime module (DO NOT MODIFY)
    sqft/
      listing-context.js                 # DOM scraper: listing ID, address, photo elements, carousel positions
      content-bridge.js                  # Thin messaging wrapper: content script <-> service worker
      photo-overlay.js                   # Hover overlay on carousel photos (scan button, room dropdown)
      photo-overlay.css                  # Styles for the overlay
  sidepanel/
    sidepanel.html                       # Side panel HTML shell
    sidepanel.js                         # Side panel logic: room cards, history, state management
    sidepanel.css                        # Side panel styles
  lib/
    shared-types.js                      # JSDoc type definitions (no runtime code)
    storage.js                           # Unused (storage is inlined in service-worker.js)
    sqft-api.js                          # Unused (API client is inlined in service-worker.js)
    geo-utils.js                         # Crime module (DO NOT MODIFY)
    utils.js                             # Crime module (DO NOT MODIFY)
  data/                                  # Crime module JSON data (DO NOT MODIFY)
  ui/
    inline-module.css                    # Crime module styles (DO NOT MODIFY)
  icons/                                 # Extension icons
```

### How the Modules Coexist

The manifest registers three `content_scripts` entries:

1. `navigation-hook.js` runs at `document_start` in the **MAIN world** (page context). It monkey-patches `history.pushState` and `history.replaceState` so the extension can detect StreetEasy's client-side navigation (it's a Next.js SPA). When the pathname changes, it dispatches a `__sleepEasyNav` custom event on `window`.

2. The crime module scripts (`geo-utils.js`, `utils.js`, `coordinates-extractor.js`, `ui-injector.js`, `main.js`) run at `document_idle` in the default isolated world. `main.js` listens for `__sleepEasyNav` and `popstate` to re-initialize on navigation.

3. The sqft module scripts (`shared-types.js`, `listing-context.js`, `content-bridge.js`, `photo-overlay.js`) also run at `document_idle` in the isolated world. `photo-overlay.js` also listens for `__sleepEasyNav` and `popstate`.

Both content script groups are independent. They share no state, no globals, no communication. The crime module uses shadow DOM; the sqft module injects overlays directly into the carousel DOM.

### Key Technical Challenges We Already Solved

**1. Content script can't intercept page's History API calls**

Chrome content scripts run in an "isolated world" — they share the DOM with the page but have separate JavaScript globals. When StreetEasy's Next.js router calls `history.pushState()`, it's calling the *page's* `history` object, not the content script's. Monkey-patching `history.pushState` inside a content script does nothing.

Solution: `navigation-hook.js` runs in `"world": "MAIN"` (the page's own JS context), where it can actually intercept the page's `history.pushState`/`replaceState`. It dispatches `__sleepEasyNav` on `window`, which content scripts *can* hear because custom events cross the world boundary via the shared DOM.

**2. React hydration destroys injected DOM elements**

StreetEasy uses Next.js with server-side rendering. After initial render, React "hydrates" the page, which can replace entire DOM subtrees. This destroys any elements we injected during initial load.

Solution: `main.js` uses a `MutationObserver` on `document.body` with `{ childList: true, subtree: true }` and checks `document.contains(container)` on every mutation. If our container disappears, we re-inject after a 300ms delay.

**3. Photo URL normalization across carousel sizes**

StreetEasy serves the same photo at different resolutions via URL suffixes:
- Thumbnail: `.../fp/HASH-se_medium_500`
- Carousel: `.../fp/HASH-se_large_800_400.webp`

To match thumbnails to carousel images (needed for position tracking), we strip the resolution suffix (`-se_medium_500`, `-se_large_800_400`) and file extension, leaving just the host + base path as a stable identifier. This is done in `listing-context.js:_getPhotoBaseHash()`.

**4. Extension loads unreliably (~1 in 3 refreshes)**

This was caused by two issues working together: the broken History API patching (fixed above) and the removal observer only watching the immediate parent node (missing grandparent replacements during React hydration). Both are now fixed.

### Data Flow

```
User hovers photo → photo-overlay.js shows overlay
User clicks "Scan" → content-bridge.js sends SCAN_PHOTO to service worker
Service worker → calls SqftEstimationAPI (currently mock, returns random but deterministic values)
Service worker → stores ScanResult + Room in chrome.storage.local
Service worker → broadcastStateChange():
  - Sends STATE_CHANGED to side panel (rooms, positions, scans)
  - Sends UPDATE_ANNOTATIONS to content script (annotations array)
photo-overlay.js receives UPDATE_ANNOTATIONS → rebuilds overlay with fresh data
sidepanel.js receives STATE_CHANGED → re-renders room cards
```

### Storage Schema (chrome.storage.local)

All state lives in `chrome.storage.local` because MV3 service workers are ephemeral. The `ListingStorage` class in `service-worker.js` manages these keys:

- `area:listings` → `{ [listingId]: Listing }` — index of all listings
- `area:rooms:{listingId}` → `Room[]` — rooms for a listing
- `area:scans:{listingId}` → `ScanResult[]` — per-photo scan results
- `area:positions:{listingId}` → `{ [photoUrl]: number }` — carousel position numbers

### Message Types

Content → Service Worker:
- `SCAN_PHOTO` — scan a single photo (auto-creates room if unassigned)
- `ADD_PHOTO_TO_ROOM` — move/add a photo to a room
- `REMOVE_PHOTO_FROM_ROOM` — remove a photo from a room
- `CREATE_ROOM` — create a new empty room
- `GET_ROOMS` — get all rooms for a listing
- `GET_ANNOTATIONS` — get annotation data for all photos in a listing
- `SET_PHOTO_POSITIONS` — send carousel position numbers to storage

Side Panel → Service Worker:
- `GET_LISTING_STATE` — get full state (listing, rooms, scans, positions)
- `ANALYZE_ROOM` — run multi-photo analysis on a room
- `RENAME_ROOM` — rename a room
- `DELETE_ROOM` — delete a room
- `GET_HISTORY` — get all listings with summary stats

Service Worker → Content:
- `UPDATE_ANNOTATIONS` — push fresh annotation array after state changes

Service Worker → Side Panel:
- `STATE_CHANGED` — push rooms, positions, scans after state changes

Side Panel → Content (via `chrome.tabs.sendMessage`):
- `GET_LISTING_CONTEXT` — ask content script for current listing ID/address/URL

### What "Annotations" Are

An annotation is a denormalized view of a photo's state, computed by `ListingStorage.getAnnotations()`. For each photo URL assigned to any room, it returns:

```javascript
{
  photoUrl,           // normalized CDN URL
  roomId,             // which room it belongs to
  roomName,           // display name of that room
  sqft,               // room-level sqft estimate (null if not analyzed)
  photoCount,         // how many photos are in this room
  pipeline,           // "single" | "multi" | null
  outdated,           // true if room photos changed since last analysis
  position,           // carousel number (e.g., 3 for "3 of 11"), or null
  photoSqft,          // per-photo scan result sqft (null if not scanned)
}
```

The photo overlay uses annotations to know: is this photo assigned? What room? What's the scan result? The side panel uses the raw `rooms`, `scans`, and `positions` data directly.

### Single-Photo vs Multi-Photo Estimation

- **Single-photo scan**: When a user clicks "Scan" on a photo, the service worker calls `estimateSinglePhoto()`. If the photo isn't assigned to a room yet, a new room is auto-created and the per-photo estimate becomes the room estimate.
- **Multi-photo analysis**: When a room has multiple photos, the user can trigger `ANALYZE_ROOM` from the sidebar. This calls `estimateMultiPhoto()` which uses all photos in the room to produce a more accurate room-level estimate.
- A room's estimate is marked `outdated: true` if photos are added/removed after analysis.
- A multi-photo room whose only estimate came from a single-photo scan is treated as "needs analysis" (the single-photo result doesn't represent the full room).

Currently the API is **entirely mocked** — `SqftEstimationAPI` in the service worker returns deterministic random numbers based on URL hashing, with artificial delays. The mock is intentional for frontend development.

---

## Part 2: What Needs to Be Implemented

### The Problem

The photo overlay (`photo-overlay.js`) has been fully updated with the user's UX feedback. The side panel (`sidepanel.js` + `sidepanel.css`) has **not** been updated to match. The sidebar currently has:

1. A separate "Analyze" / "Re-analyze" button as its own element below the photo list
2. Photo items that show `Photo N` text but no per-photo scan results
3. No `scans` data wired into the state management

The user's explicit feedback (direct quotes):

> "the analyze room/scan buttons should just both offer the ability to rescan easily and should include the results or the --- indicating none in the same ux element. no need for a whole additional ux element here"

> "photos should be ided by their position in the carousel and even if the overall room analysis is undetermined we can have the per photo number still listed in an unemphasized way"

In other words: **merge the analyze action into the result display** (no separate button), and **show per-photo scan results inline next to each photo's carousel number**.

The overlay already implements this pattern — the scan button IS the result display. It shows "Scan" (unscanned), "185 sqft" (scanned), or "---" (assigned but no result), and is always clickable to (re)scan. The sidebar needs the equivalent treatment at both the photo level and the room level.

### Exact Changes Required

#### 1. Wire `scans` data into sidepanel.js state management

The service worker already returns `scans` in both `GET_LISTING_STATE` (line 454) and `STATE_CHANGED` broadcasts (line 284). The sidepanel already has the state variable declared (line 18: `let scans = {};`), but it's never populated.

**In `loadCurrentListing()` (line 64):**

After line 100 (`positions = state?.positions || {};`), add:
```javascript
// Build scan lookup: photoUrl -> ScanResult
const scanList = state?.scans || [];
scans = {};
for (const s of scanList) {
  scans[s.photoUrl] = s;
}
```

**In `listenForUpdates()` (line 110):**

Inside the `STATE_CHANGED` handler (after line 116 `if (msg.positions) positions = msg.positions;`), add:
```javascript
if (msg.scans) {
  scans = {};
  for (const s of msg.scans) {
    scans[s.photoUrl] = s;
  }
}
```

**In `renderEmptyState()` (line 129):**

After line 133 (`positions = {};`), add:
```javascript
scans = {};
```

#### 2. Rewrite `renderRoomCard()` (currently line 180)

Replace the entire function. Here is what the new version should do:

**Room header row:** Room name input (editable), room-level sqft display (clickable to analyze/re-analyze), delete button.

The room-level sqft display replaces the old separate analyze button. It should be a single clickable element that:
- Shows the sqft value + unit if there's a valid estimate: `"285 sqft"` (styled prominent, clickable to re-analyze)
- Shows `"???"` if no estimate exists but room has photos (clickable to trigger analysis)
- Shows `"Analyzing..."` with a small spinner if `analyzingRoomId === room.id`
- When a multi-photo room only has a single-pipeline estimate, or is outdated, show the value but with a visual hint (e.g., lower opacity or a small re-analyze icon) and keep it clickable

The `data-action="analyze"` attribute should be on this element so the existing `attachRoomListeners` wiring works.

**Photo list:** Each photo shows its carousel position number and per-photo scan result inline.

For each `photoUrl` in `room.photoUrls`:
```javascript
const pos = positions[photoUrl];
const scan = scans[photoUrl];
const posLabel = pos ? `#${pos}` : '#?';
let scanLabel;
if (scan && scan.estimatedSqft !== null && scan.estimatedSqft !== undefined) {
  scanLabel = `${scan.estimatedSqft} sqft`;
} else {
  scanLabel = '---';
}
```

Each photo item should render as a small pill/chip:
```
#3  185 sqft  ×
#7  ---       ×
#?  ---       ×
```

Where `#3` is the carousel position (styled muted/secondary), `185 sqft` or `---` is the per-photo scan result (styled normal weight, not emphasized — this is supplementary info), and `×` is the remove-from-room button.

If a photo has no scan result at all (not even `---`), still show the position number. The user said: "even if the overall room analysis is undetermined we can have the per photo number still listed in an unemphasized way."

**No separate actions div.** No `<div class="room-actions">`. No standalone analyze button. The analyze affordance is built into the sqft display in the header.

Here's the concrete HTML structure for the new `renderRoomCard()`:

```javascript
function renderRoomCard(room) {
  const isAnalyzing = analyzingRoomId === room.id;
  const hasPhotos = room.photoUrls.length > 0;
  const isMulti = room.photoUrls.length > 1;
  const hasEstimate = room.estimatedSqft !== null;
  // A single-pipeline estimate on a multi-photo room isn't a valid room estimate
  const hasValidEstimate = hasEstimate && !(isMulti && room.pipeline === 'single');

  // Room-level sqft display (doubles as analyze trigger)
  let sqftHtml;
  if (isAnalyzing) {
    sqftHtml = `<button class="room-sqft-btn analyzing" data-room-id="${room.id}" data-action="analyze" disabled>
      <span class="spinner-sm"></span>
    </button>`;
  } else if (hasValidEstimate && !room.outdated) {
    sqftHtml = `<button class="room-sqft-btn has-result" data-room-id="${room.id}" data-action="analyze" title="Click to re-analyze">
      ${room.estimatedSqft} <span class="unit">sqft</span>
    </button>`;
  } else if (hasValidEstimate && room.outdated) {
    sqftHtml = `<button class="room-sqft-btn has-result outdated" data-room-id="${room.id}" data-action="analyze" title="Photos changed - click to re-analyze">
      ${room.estimatedSqft} <span class="unit">sqft</span>
    </button>`;
  } else if (hasPhotos) {
    sqftHtml = `<button class="room-sqft-btn placeholder" data-room-id="${room.id}" data-action="analyze" title="Click to analyze">
      ???
    </button>`;
  } else {
    sqftHtml = `<span class="room-sqft-btn placeholder disabled">---</span>`;
  }

  // Per-photo list
  let photosHtml = '';
  if (hasPhotos) {
    const items = room.photoUrls.map(url => {
      const pos = positions[url];
      const scan = scans[url];
      const posLabel = pos ? `#${pos}` : '#?';
      let resultLabel;
      if (scan && scan.estimatedSqft !== null && scan.estimatedSqft !== undefined) {
        resultLabel = `<span class="photo-scan-result">${scan.estimatedSqft} <span class="unit">sqft</span></span>`;
      } else {
        resultLabel = `<span class="photo-scan-result muted">---</span>`;
      }
      return `
        <div class="room-photo-item">
          <span class="room-photo-pos">${posLabel}</span>
          ${resultLabel}
          <button class="room-photo-remove" data-room-id="${room.id}"
                  data-photo-url="${escapeHtml(url)}" data-action="remove-photo"
                  title="Remove from room">&times;</button>
        </div>
      `;
    }).join('');
    photosHtml = `<div class="room-photo-list">${items}</div>`;
  }

  return `
    <div class="room-card" data-room-id="${room.id}">
      <div class="room-header">
        <input class="room-name" type="text" value="${escapeHtml(room.name)}"
               data-room-id="${room.id}" data-action="rename" />
        ${sqftHtml}
        <button class="room-delete" data-room-id="${room.id}" data-action="delete"
                title="Delete room">&times;</button>
      </div>
      ${photosHtml}
    </div>
  `;
}
```

#### 3. Update sidebar CSS

**Remove these classes** (no longer used):
- `.room-actions` — the wrapper div is gone
- `.analyze-btn` and all its states (`.analyze-btn:hover`, `:disabled`, `.loading`, `.spinner-sm` inside it) — replaced by `.room-sqft-btn`
- `.outdated-badge` — replaced by `.room-sqft-btn.outdated`
- `.room-sqft-inline` — no longer a separate wrapper

**Keep `.spinner-sm`** and its animation — it's used in the new analyzing state.

**Add these new classes:**

```css
/* Room-level sqft button (replaces separate analyze button) */
.room-sqft-btn {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 8px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: none;
  font-size: 13px;
  font-weight: 900;
  color: #111827;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
  white-space: nowrap;
  flex: 0 0 auto;
}

.room-sqft-btn:hover {
  background: #f3f4f6;
  border-color: #e5e7eb;
}

.room-sqft-btn.has-result {
  /* Confident result - prominent but still clickable for re-analyze */
}

.room-sqft-btn.outdated {
  opacity: 0.6;
}

.room-sqft-btn.placeholder {
  color: #9ca3af;
  font-weight: 700;
}

.room-sqft-btn.disabled {
  cursor: default;
  opacity: 0.4;
}

.room-sqft-btn.disabled:hover {
  background: none;
  border-color: transparent;
}

.room-sqft-btn.analyzing {
  cursor: wait;
  opacity: 0.5;
}

.room-sqft-btn .unit {
  font-size: 11px;
  font-weight: 600;
  color: #6b7280;
}
```

**Update `.room-photo-item`** to accommodate the new structure:

```css
.room-photo-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  background: #f3f4f6;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  color: #374151;
}

/* Carousel position label (#3, #7, etc.) */
.room-photo-pos {
  color: #9ca3af;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  min-width: 20px;
}

/* Per-photo scan result */
.photo-scan-result {
  font-variant-numeric: tabular-nums;
}

.photo-scan-result.muted {
  color: #9ca3af;
}

.photo-scan-result .unit {
  font-size: 9px;
  font-weight: 600;
  color: #9ca3af;
}
```

**Keep** `.room-sqft .pipeline-tag` and `.room-sqft .unit` — actually these are no longer used either since we replaced `<span class="room-sqft">` with `<button class="room-sqft-btn">`. Remove them.

#### 4. Remove the old `.room-sqft`, `.room-sqft.placeholder`, `.room-sqft .unit`, `.room-sqft .pipeline-tag` styles

These were used by the old `renderRoomCard` and are replaced by `.room-sqft-btn` variants.

### Summary of Files to Edit

| File | What to do |
|------|-----------|
| `sidepanel/sidepanel.js` | Wire `scans` into `loadCurrentListing()` and `listenForUpdates()` and `renderEmptyState()`. Rewrite `renderRoomCard()` entirely. |
| `sidepanel/sidepanel.css` | Remove `.room-actions`, `.analyze-btn*`, `.outdated-badge*`, `.room-sqft`, `.room-sqft-inline`, `.room-sqft .pipeline-tag`. Add `.room-sqft-btn*`, `.room-photo-pos`, `.photo-scan-result*`. Update `.room-photo-item`. |

**Do not modify** any other files. The overlay, service worker, content bridge, listing context, navigation hook, and crime module are all in their final state.

### How to Test

1. Load the extension in Chrome (`chrome://extensions` → Load unpacked → select the `extension/` directory)
2. Navigate to any StreetEasy listing (e.g., `https://streeteasy.com/building/some-building/1a`)
3. Hover over photos in the carousel — the overlay should appear with "Scan" button and room assignment
4. Click "Scan" on a few photos — they should auto-create rooms and show mock sqft results
5. Open the side panel (click the extension icon or it auto-opens on first scan)
6. Verify room cards show:
   - Room name (editable)
   - Room-level sqft as a clickable element (not a separate button)
   - Per-photo items showing `#N sqft` or `#N ---`
   - Remove buttons on each photo
7. Add multiple photos to one room via the overlay dropdown, then click the room-level sqft display to trigger multi-photo analysis
8. Navigate between listings (click through StreetEasy) — verify state updates correctly
9. Check the History tab in the side panel

### Design Philosophy Notes from the User

The user's core UX principle: **don't add separate UI elements for actions when the result display can serve as the action trigger.** The scan button IS the result. The sqft display IS the analyze button. "No need for a whole additional UX element here." Keep it compact, information-dense, and dual-purpose.

The carousel already shows "2 of 11" pagination — we don't duplicate that in the overlay. We only show position numbers in the sidebar (where the carousel isn't visible) as `#N` labels.

Photo-level scan results are supplementary/unemphasized. The room-level analysis is the primary number. But showing per-photo results gives the user confidence that scans happened and provides a sense of what each photo contributed.
