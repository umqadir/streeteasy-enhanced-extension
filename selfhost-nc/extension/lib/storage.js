/**
 * SleepEasy Area Analysis - Storage Layer
 *
 * Abstraction over chrome.storage.local for rooms, listings, and scan results.
 * All state mutations go through the service worker; content scripts and the
 * side panel read via message passing, not directly.
 *
 * Storage schema:
 *   "area:listings"           -> { [listingId]: Listing }
 *   "area:rooms:{listingId}"  -> Room[]
 *   (legacy) "area:scans:{listingId}" -> ScanResult[] (no longer used)
 */

class ListingStorage {
  // ── helpers ──

  /** @param {string} key @returns {Promise<any>} */
  async _get(key) {
    const result = await chrome.storage.local.get(key);
    return result[key] ?? null;
  }

  /** @param {string} key @param {any} value */
  async _set(key, value) {
    await chrome.storage.local.set({ [key]: value });
  }

  /** @param {string} key */
  async _remove(key) {
    await chrome.storage.local.remove(key);
  }

  _roomsKey(listingId) { return `area:rooms:${listingId}`; }

  // ── Listings ──

  /** @returns {Promise<Object.<string, Listing>>} */
  async getListingsIndex() {
    return (await this._get('area:listings')) || {};
  }

  /** @returns {Promise<Listing[]>} */
  async getListings() {
    const idx = await this.getListingsIndex();
    return Object.values(idx).sort((a, b) => b.updatedAt - a.updatedAt);
  }

  /** @param {string} listingId @returns {Promise<Listing|null>} */
  async getListing(listingId) {
    const idx = await this.getListingsIndex();
    return idx[listingId] || null;
  }

  /** @param {Listing} listing */
  async saveListing(listing) {
    const idx = await this.getListingsIndex();
    idx[listing.id] = listing;
    await this._set('area:listings', idx);
  }

  /** Ensure a listing record exists; create if not. @returns {Promise<Listing>} */
  async ensureListing(listingId, url, address) {
    let listing = await this.getListing(listingId);
    if (!listing) {
      listing = {
        id: listingId,
        url: url || '',
        address: address || null,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      };
      await this.saveListing(listing);
    }
    return listing;
  }

  /** @param {string} listingId */
  async deleteListing(listingId) {
    const idx = await this.getListingsIndex();
    delete idx[listingId];
    await this._set('area:listings', idx);
    await this._remove(this._roomsKey(listingId));
  }

  // ── Rooms ──

  /** @param {string} listingId @returns {Promise<Room[]>} */
  async getRooms(listingId) {
    return (await this._get(this._roomsKey(listingId))) || [];
  }

  /** @param {string} listingId @param {string} roomId @returns {Promise<Room|null>} */
  async getRoom(listingId, roomId) {
    const rooms = await this.getRooms(listingId);
    return rooms.find(r => r.id === roomId) || null;
  }

  /** @param {string} listingId @param {Room[]} rooms */
  async _saveRooms(listingId, rooms) {
    await this._set(this._roomsKey(listingId), rooms);
    // Touch listing updatedAt
    const listing = await this.getListing(listingId);
    if (listing) {
      listing.updatedAt = Date.now();
      await this.saveListing(listing);
    }
  }

  /**
   * Create a new room with an auto-generated name.
   * @param {string} listingId
   * @param {string} [name]
   * @returns {Promise<Room>}
   */
  async createRoom(listingId, name) {
    const rooms = await this.getRooms(listingId);
    const roomNum = rooms.length + 1;
    const room = {
      id: crypto.randomUUID(),
      listingId,
      name: name || `Room ${roomNum}`,
      photoUrls: [],
      estimatedSqft: null,
      pipeline: null,
      analyzedAt: null,
      outdated: false,
    };
    rooms.push(room);
    await this._saveRooms(listingId, rooms);
    return room;
  }

  /** @param {string} listingId @param {string} roomId @param {Partial<Room>} updates */
  async updateRoom(listingId, roomId, updates) {
    const rooms = await this.getRooms(listingId);
    const idx = rooms.findIndex(r => r.id === roomId);
    if (idx === -1) return null;
    Object.assign(rooms[idx], updates);
    await this._saveRooms(listingId, rooms);
    return rooms[idx];
  }

  /** @param {string} listingId @param {string} roomId */
  async deleteRoom(listingId, roomId) {
    let rooms = await this.getRooms(listingId);
    rooms = rooms.filter(r => r.id !== roomId);
    await this._saveRooms(listingId, rooms);
  }

  // ── Photo assignment ──

  /** @param {string} listingId @param {string} roomId @param {string} photoUrl */
  async addPhotoToRoom(listingId, roomId, photoUrl) {
    const rooms = await this.getRooms(listingId);
    const room = rooms.find(r => r.id === roomId);
    if (!room) return;
    if (room.photoUrls.includes(photoUrl)) return;
    room.photoUrls.push(photoUrl);
    if (room.analyzedAt) room.outdated = true;
    await this._saveRooms(listingId, rooms);
  }

  /** @param {string} listingId @param {string} roomId @param {string} photoUrl */
  async removePhotoFromRoom(listingId, roomId, photoUrl) {
    const rooms = await this.getRooms(listingId);
    const room = rooms.find(r => r.id === roomId);
    if (!room) return;
    room.photoUrls = room.photoUrls.filter(u => u !== photoUrl);
    if (room.analyzedAt) room.outdated = true;
    await this._saveRooms(listingId, rooms);
  }

  /**
   * Find which room (if any) a photo belongs to in this listing.
   * @param {string} listingId
   * @param {string} photoUrl
   * @returns {Promise<Room|null>}
   */
  async findRoomForPhoto(listingId, photoUrl) {
    const rooms = await this.getRooms(listingId);
    return rooms.find(r => r.photoUrls.includes(photoUrl)) || null;
  }

  // ── Room analysis ──

  /**
   * Update a room's sqft estimate after analysis.
   * @param {string} listingId
   * @param {string} roomId
   * @param {number} sqft
   * @param {string} pipeline - "single" | "multi"
   */
  async updateRoomEstimate(listingId, roomId, sqft, pipeline) {
    await this.updateRoom(listingId, roomId, {
      estimatedSqft: sqft,
      pipeline,
      analyzedAt: Date.now(),
      outdated: false,
    });
  }

  // ── Annotations (computed, not stored) ──

  /**
   * Build annotation data for all photos in a listing.
   * @param {string} listingId
   * @returns {Promise<PhotoAnnotation[]>}
   */
  async getAnnotations(listingId) {
    const rooms = await this.getRooms(listingId);
    const annotations = [];
    for (const room of rooms) {
      for (const photoUrl of room.photoUrls) {
        annotations.push({
          photoUrl,
          roomId: room.id,
          roomName: room.name,
          sqft: room.estimatedSqft,
          photoCount: room.photoUrls.length,
        });
      }
    }
    return annotations;
  }
}

// Make available in all contexts
if (typeof window !== 'undefined') {
  window.ListingStorage = ListingStorage;
}
