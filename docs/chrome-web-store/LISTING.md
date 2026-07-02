# Chrome Web Store Listing — SleepEasy

Paste-ready fields for the Web Store submission. The store build does both
features fully on-device: crime stats from bundled data, and room square-footage
from an in-browser computer-vision model. No localhost, no accounts.

---

## Store listing tab

**Item name**
```
SleepEasy — Crime stats & room sizes for StreetEasy
```

**Summary** (132 characters max)
```
Adds NYC crime stats and on-device AI room square-footage estimates to StreetEasy listings. Private: runs on your device.
```

**Category:** Shopping

**Language:** English (United States)

**Detailed description**
```
SleepEasy adds neighborhood crime statistics and room square-footage estimates to StreetEasy NYC listing pages. Everything runs on your device — no accounts, no tracking, no data sent anywhere.

CRIME STATISTICS
• Murder, felony assault, and property crime for the listing's neighborhood
• Ranked across all 197 NYC neighborhoods, with comparison to the citywide rate
• Four measures (ambient risk index, per 100k residents, per square mile, raw counts) and three time windows
• Built from NYPD complaint data via NYC Open Data; computed in your browser, no network calls

ROOM SQUARE FOOTAGE (ON-DEVICE)
• Group a listing's photos into rooms and estimate each room's floor area
• A computer-vision model runs in your browser (WebGPU, with a CPU fallback): floor segmentation, metric depth, floor-plane fitting, and wall-to-wall layout completion that recovers floor hidden by furniture
• The model downloads once (about 150 MB) from Hugging Face and is cached; your photos are analyzed locally and never sent to a server
• A higher-accuracy multi-photo mode is available via an optional local backend (open-source, installed separately)

PRIVACY
• No accounts, no analytics, no tracking
• Photos are analyzed on your device; no listing or personal data is transmitted
• NYC only

This is a personal research project. Square-footage estimates are approximate: the extension estimates wall-to-wall floor area and falls back to visible floor when a room's layout cannot be confidently completed. Crime statistics are informational; past incidence does not predict future safety. Independent project, not affiliated with or endorsed by StreetEasy, Zillow Group, the NYPD, or the City of New York.

Source: https://github.com/umqadir/streeteasy-enhanced-extension
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
SleepEasy augments StreetEasy NYC listing pages with neighborhood crime statistics and on-device room square-footage estimates.
```

**Permission justifications**

| Permission | Justification to paste |
|---|---|
| `storage` | Saves the user's room/photo groupings and extension settings locally on their device. No data leaves the browser. |
| `sidePanel` | Provides the side-panel UI where users group listing photos into rooms and view square-footage estimates. |
| `activeTab` | Lets the extension read the current StreetEasy listing the user is viewing so it can show stats for that listing. |
| Host access to `streeteasy.com` (content scripts) | The extension only runs on StreetEasy listing pages, where it reads listing details and injects the crime module and photo controls. |
| Host access to `photos.zillowstatic.com` / `images.streeteasy.com` | Fetches the listing's own photos so the room-area model can analyze them on the user's device. The images are not stored or transmitted anywhere. |
| Host access to `huggingface.co` / `hf.co` | Downloads the open-source computer-vision model files (data, not code) once, then caches them. No user data is sent. |

**Remote code:** No — all executable code (JavaScript and WebAssembly) ships in the package. Only model weight files (data) are fetched from Hugging Face and cached.

**Data usage disclosures** — the extension does **not** collect or use any category:
- Personally identifiable information — No
- Health information — No
- Financial and payment information — No
- Authentication information — No
- Personal communications — No
- Location — No (it reads a listing's location from the page to map it to a neighborhood, but does not collect or transmit any location data)
- Web history — No
- User activity — No
- Website content — No (listing photos are fetched and analyzed transiently on-device, not collected or transmitted)

**Certifications** (check all):
- I do not sell or transfer user data to third parties, outside of approved use cases — ✔
- I do not use or transfer user data for purposes unrelated to my item's single purpose — ✔
- I do not use or transfer user data to determine creditworthiness or for lending purposes — ✔

**Privacy policy URL**
```
https://umqadir.github.io/streeteasy-enhanced-extension/privacy.html
```
