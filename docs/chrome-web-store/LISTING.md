# Chrome Web Store Listing — SleepEasy

Every field needed for the Web Store submission, ready to paste. Screenshots are in [`screenshots/`](screenshots/); the upload package is built by `selfhost/` (see [SUBMIT.md](SUBMIT.md)).

---

## Store listing tab

**Item name**
```
SleepEasy — Crime stats & room sizes for StreetEasy
```
(If the field caps at 45 characters, use: `SleepEasy for StreetEasy`)

**Summary** (132 characters max)
```
Adds NYPD crime stats and AI room square-footage estimates to StreetEasy NYC listings. Free, private, runs on your device.
```

**Category:** Shopping (Productivity is an acceptable alternative)

**Language:** English (United States)

**Detailed description**
```
SleepEasy adds two things StreetEasy doesn't show you, right on the listing page — and it does it without accounts, tracking, or sending your data anywhere.

CRIME CONTEXT, INLINE ON EVERY LISTING
A small module appears directly on StreetEasy NYC listing pages, designed to feel native rather than bolted on:
• Murder, felony assault, and property-crime figures for the listing's neighborhood
• Four ways to read them: an "ambient risk index" (incidents per 100,000 people actually present — residents plus daytime workers, so business-heavy areas aren't distorted), per 100,000 residents, per square mile, and raw counts
• Each metric is ranked against all 197 NYC neighborhoods
• Built from public NYPD complaint data via NYC Open Data

There's no setup for this part. The data ships inside the extension and every lookup happens in your browser — nothing is sent to any server.

ROOM SQUARE FOOTAGE, FROM THE LISTING'S PHOTOS
NYC listings love to skip square footage. SleepEasy's side panel lets you group a listing's photos into rooms and estimate each room's floor area using a computer-vision pipeline:
• Semantic segmentation isolates the floor
• A metric-depth model recovers the room's 3D geometry and camera intrinsics
• The floor plane is measured in real-world units

This feature runs on a local backend you install on your own machine (one command), so your photos are analyzed locally and never sent to a third-party service. About 5% error in multi-photo mode on an NVIDIA GPU, about 20% in single-image mode on any computer. The full accuracy benchmark is published in the open-source repository.

PRIVATE BY DESIGN
• No accounts, no sign-in
• No analytics, no tracking, no advertising
• No data collected, transmitted, or sold
• NYC only — focused on StreetEasy listings mapped to official NYC neighborhoods

Open source (MIT). SleepEasy is an independent project and is not affiliated with or endorsed by StreetEasy, Zillow Group, the NYPD, or the City of New York. Crime statistics are informational; past incidence does not predict future safety.

Source code, benchmarks, and the optional backend installer:
https://github.com/umqadir/streeteasy-enhanced-extension
```

**Homepage / website URL**
```
https://umqadir.github.io/streeteasy-enhanced-extension/
```

**Support URL**
```
https://github.com/umqadir/streeteasy-enhanced-extension/issues
```

**Screenshots** (1280×800, in `screenshots/`)
1. `01-crime-in-context.png` — crime module on a real listing
2. `02-room-sqft.png` — side panel room estimate
3. `03-how-it-works.png` — floor segmentation / computer vision

---

## Privacy practices tab

**Single purpose** (required)
```
SleepEasy augments StreetEasy NYC listing pages with neighborhood crime statistics and optional, locally-computed room square-footage estimates.
```

**Permission justifications**

| Permission | Justification to paste |
|---|---|
| `storage` | Saves the user's room/photo groupings and extension settings locally on their device. No data leaves the browser. |
| `sidePanel` | Provides the side-panel UI where users group listing photos into rooms and view square-footage estimates. |
| `activeTab` | Lets the extension read the current StreetEasy listing the user is viewing so it can show stats for that listing. |
| Host access to `streeteasy.com` (content scripts) | The extension only runs on StreetEasy listing pages, where it reads listing details and injects the crime module and photo controls. |
| Host access to `127.0.0.1` / `localhost` | The optional square-footage feature talks to a local backend the user runs on their own machine. Used only for localhost; no remote servers are contacted. |

**Remote code:** No — the extension does not load or execute remote code. All code is in the package.

**Data usage disclosures** — declare that the extension does **not** collect or use any of the listed categories:
- Personally identifiable information — No
- Health information — No
- Financial and payment information — No
- Authentication information — No
- Personal communications — No
- Location — No (the extension reads a listing's location from the page to map it to a neighborhood, but does not collect or transmit the user's own location or any location data)
- Web history — No
- User activity — No
- Website content — No (page content is read transiently in the browser and not collected or transmitted)

**Certifications** (check all):
- I do not sell or transfer user data to third parties, outside of approved use cases — ✔
- I do not use or transfer user data for purposes unrelated to my item's single purpose — ✔
- I do not use or transfer user data to determine creditworthiness or for lending purposes — ✔

**Privacy policy URL**
```
https://umqadir.github.io/streeteasy-enhanced-extension/privacy.html
```
