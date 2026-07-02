# Submitting SleepEasy to the Chrome Web Store

Everything is staged. The remaining steps require the account owner: signing in,
the one-time developer fee, and the final Submit click. Budget ~15 minutes.

The store build runs both features on-device (crime stats + in-browser room
sizes). No localhost permission.

## What's prepared

- **Upload package:** `sleepeasy-chrome-store-v2.2.0.zip` — built by `scripts/build-extension.sh` into `dist/`, also copied to `~/Downloads`. Flat zip (manifest at the root).
- **Listing copy:** every field, ready to paste — [LISTING.md](LISTING.md)
- **Screenshots:** three 1280×800 PNGs — [screenshots/](screenshots/)
- **Privacy policy (live):** https://umqadir.github.io/streeteasy-enhanced-extension/privacy.html

## Steps

1. **Sign in** to the [Developer Dashboard](https://chrome.google.com/webstore/devconsole) as `uzairq93@gmail.com`.
2. **One-time registration** (first publish only): pay the **$5 USD** fee, accept the developer agreement.
3. **+ New item** → upload `sleepeasy-chrome-store-v2.2.0.zip`.
4. **Store listing** — paste from [LISTING.md](LISTING.md): name, summary, description, category (Shopping), language; upload the three screenshots; set homepage and support URLs.
5. **Privacy practices** — paste the single-purpose statement and the six permission justifications; mark every data-collection category "No"; set Remote code = No; check the three certifications; paste the privacy policy URL.
6. **Distribution:** Public.
7. **Submit for review.**

## If the reviewer pushes back

- **"Why fetch from huggingface.co?"** — The room-area feature downloads the open-source CV model **weights** (data files) once, then caches them. No executable code is fetched (the JS/WASM runtime is in the package), and no user data is sent. This is the standard pattern for on-device ML extensions.
- **"Why access the photo CDN?"** — To fetch the listing's own photos so the model can measure floor area on the user's device. The images are not stored or transmitted.
- **Single purpose** — both features add decision context to a StreetEasy listing page; point to the single-purpose statement.
- **Affiliation** — listing, privacy policy, and landing page carry the non-affiliation disclaimer.

## Two builds

- **Web Store** (this doc) — crime stats + on-device single-image room sizes. No backend, no localhost.
- **GitHub** — `Load unpacked` build adds the optional local Python backend, which unlocks higher-accuracy multi-photo room sizes (DUSt3R) and can also serve single-image. It keeps a `localhost` permission and can't ship through the Web Store (it talks to a local process).
