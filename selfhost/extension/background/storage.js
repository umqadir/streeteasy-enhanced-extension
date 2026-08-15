/**
 * SleepEasy listing/photo storage.
 *
 * Stored area estimates are photo-level. A label is only an optional room name
 * for organizing a photo; it does not merge multiple photos into one estimate.
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

  _photosKey(id) { return `area:photos:${id}`; }
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

  async _touchListing(listingId) {
    const listing = await this.getListing(listingId);
    if (listing) {
      listing.updatedAt = Date.now();
      await this.saveListing(listing);
    }
  }

  async deleteListing(listingId) {
    const idx = await this.getListingsIndex();
    delete idx[listingId];
    await this._set('area:listings', idx);
    await this._remove(this._photosKey(listingId));
    await this._remove(this._positionsKey(listingId));
  }

  async getPhotos(listingId) {
    return (await this._get(this._photosKey(listingId))) || [];
  }

  async getPhoto(listingId, photoId) {
    const photos = await this.getPhotos(listingId);
    return photos.find(p => p.id === photoId) || null;
  }

  async findPhotoByUrl(listingId, photoUrl) {
    const photos = await this.getPhotos(listingId);
    return photos.find(p => p.photoUrl === photoUrl) || null;
  }

  async _savePhotos(listingId, photos) {
    await this._set(this._photosKey(listingId), photos);
    await this._touchListing(listingId);
  }

  async savePhoto(listingId, photoUrl, label = '') {
    const photos = await this.getPhotos(listingId);
    const existing = photos.find(p => p.photoUrl === photoUrl);
    const cleanLabel = String(label || '').trim();

    if (existing) {
      existing.label = cleanLabel;
      await this._savePhotos(listingId, photos);
      return existing;
    }

    const photo = {
      id: crypto.randomUUID(),
      listingId,
      photoUrl,
      label: cleanLabel,
      estimatedSqft: null,
      pipeline: null,
      analyzedAt: null,
    };
    photos.push(photo);
    await this._savePhotos(listingId, photos);
    return photo;
  }

  async updatePhoto(listingId, photoId, updates) {
    const photos = await this.getPhotos(listingId);
    const idx = photos.findIndex(p => p.id === photoId);
    if (idx === -1) return null;
    Object.assign(photos[idx], updates);
    await this._savePhotos(listingId, photos);
    return photos[idx];
  }

  async labelPhoto(listingId, photoId, label) {
    return this.updatePhoto(listingId, photoId, { label: String(label || '').trim() });
  }

  async deletePhoto(listingId, photoId) {
    const photos = (await this.getPhotos(listingId)).filter(p => p.id !== photoId);
    await this._savePhotos(listingId, photos);
  }

  async updatePhotoEstimate(listingId, photoId, sqft, pipeline) {
    await this.updatePhoto(listingId, photoId, {
      estimatedSqft: sqft,
      pipeline,
      analyzedAt: Date.now(),
    });
  }

  async getLabels(listingId) {
    const labels = new Set();
    for (const photo of await this.getPhotos(listingId)) {
      const label = String(photo.label || '').trim();
      if (label) labels.add(label);
    }
    return [...labels].sort((a, b) => a.localeCompare(b));
  }

  async getAnnotations(listingId) {
    const photos = await this.getPhotos(listingId);
    const positions = await this.getPhotoPositions(listingId);

    return photos.map(photo => ({
      photoUrl: photo.photoUrl,
      photoId: photo.id,
      label: photo.label || '',
      sqft: photo.estimatedSqft,
      pipeline: photo.pipeline || null,
      analyzedAt: photo.analyzedAt || null,
      position: positions[photo.photoUrl] || null,
    }));
  }

  async getPhotoPositions(listingId) {
    return (await this._get(this._positionsKey(listingId))) || {};
  }

  async setPhotoPositions(listingId, positionsMap) {
    const existing = await this.getPhotoPositions(listingId);
    Object.assign(existing, positionsMap);
    await this._set(this._positionsKey(listingId), existing);
  }
}
