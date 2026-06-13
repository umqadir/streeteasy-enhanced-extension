/**
 * SleepEasy listing/room storage.
 *
 * Single owner of all persisted sqft-analysis state. Content scripts and the
 * side panel never touch chrome.storage directly — they go through the
 * service worker's message API, which delegates here.
 *
 * Shapes:
 *   Listing: { id, url, address, createdAt, updatedAt }
 *   Room:    { id, listingId, name, photoUrls[], estimatedSqft, pipeline,
 *              analyzedAt, outdated }
 *   PhotoAnnotation (derived): { photoUrl, roomId, roomName, sqft, photoCount,
 *                                pipeline, outdated, position }
 */

export class ListingStorage {
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
  _positionsKey(id) { return `area:positions:${id}`; }

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
    await this._remove(this._positionsKey(listingId));
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
    const trimmed = String(name || '').trim();
    const room = {
      id: crypto.randomUUID(), listingId, name: trimmed || `Room ${rooms.length + 1}`,
      photoUrls: [], estimatedSqft: null, pipeline: null, analyzedAt: null, outdated: false,
    };
    rooms.push(room);
    await this._saveRooms(listingId, rooms);
    return room;
  }

  /**
   * Any mutation to a room's photo set invalidates its area estimate.
   * Product requirement: no stale sqft after adds/removes/moves.
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
  async renameRoom(listingId, roomId, name) {
    const trimmed = String(name || '').trim();
    if (!trimmed) throw new Error('Room name cannot be empty');
    return this.updateRoom(listingId, roomId, { name: trimmed });
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
    const positions = await this.getPhotoPositions(listingId);

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

  // Photo position tracking (photoUrl -> carousel number)
  async getPhotoPositions(listingId) {
    return (await this._get(this._positionsKey(listingId))) || {};
  }
  async setPhotoPositions(listingId, positionsMap) {
    const existing = await this.getPhotoPositions(listingId);
    Object.assign(existing, positionsMap);
    await this._set(this._positionsKey(listingId), existing);
  }
}
