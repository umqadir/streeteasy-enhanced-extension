# Submitting SleepEasy to the Chrome Web Store

Everything is staged. The only steps left are the ones that legally require you (the account owner): signing in, the one-time developer fee, and the final Publish click. Budget ~15 minutes.

## What's already prepared

- **Upload package:** `sleepeasy-chrome-store-v1.1.0.zip` — attached to the [v1.1.0 release](https://github.com/umqadir/streeteasy-enhanced-extension/releases/tag/v1.1.0) and copied to your `~/Downloads`. This is the **flat** zip (manifest at the root) the Web Store requires — not the same as the "Load unpacked" zip.
- **Listing copy:** every field, ready to paste — [LISTING.md](LISTING.md)
- **Screenshots:** three 1280×800 PNGs — [screenshots/](screenshots/)
- **Privacy policy (live):** https://umqadir.github.io/streeteasy-enhanced-extension/privacy.html

## Steps

1. **Sign in** to the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole) as `uzairq93@gmail.com`. (Google asked to re-verify your identity, which is why this can't be automated.)

2. **One-time developer registration**, if you've never published before: pay the **$5 USD** fee and accept the developer agreement. This is a one-time account fee, not per-extension.

3. Click **+ New item** and **upload** `sleepeasy-chrome-store-v1.1.0.zip` (from `~/Downloads`).

4. **Store listing tab** — paste from [LISTING.md](LISTING.md): name, summary, detailed description, category (Shopping), language. Upload the three screenshots from [screenshots/](screenshots/). Set homepage and support URLs.

5. **Privacy practices tab** — paste from [LISTING.md](LISTING.md): single-purpose description; the per-permission justifications; mark every data-collection category as "No"; check the three certifications; paste the privacy policy URL.

6. **Distribution:** Public, all regions (or your preference).

7. Click **Submit for review.** Review typically takes a few hours to a few business days. You'll get an email when it's published.

## If the reviewer pushes back

The most likely questions and the honest answers:

- **"Why localhost permission?"** — The optional square-footage feature uses a local backend the user installs themselves; it only ever contacts `127.0.0.1`. (Already in the permission justifications.)
- **"Single purpose unclear?"** — Both features serve one purpose: giving a StreetEasy shopper more decision context on the listing page. Point to the single-purpose statement.
- **Trademark/affiliation** — The listing, privacy policy, and landing page all carry the non-affiliation disclaimer; the name "SleepEasy" doesn't impersonate StreetEasy.

## Note on the two install paths

This is intentional and both stay live:
- **Web Store** (this doc) — the easy, one-click path for the crime-stats feature, for non-technical users.
- **GitHub release** — `Load unpacked` + the optional local backend for square footage, which can't ship through the Web Store because it needs a local Python process.
