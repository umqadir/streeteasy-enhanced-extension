# Chrome Web Store Listing - SleepEasy

Paste-ready fields for the Web Store submission. The store build does both
features fully on-device: crime stats from bundled data, and photo-based room
area estimates from a local model. No localhost, no accounts.

---

## Store listing tab

**Item name**
```
SleepEasy - Crime stats and room sizes for StreetEasy
```

**Summary** (132 characters max)
```
Adds NYC crime stats and local room-size estimates to StreetEasy listing pages.
```

**Category:** Shopping

**Language:** English (United States)

**Detailed description**
```
SleepEasy adds neighborhood crime stats and photo-based room-size estimates to StreetEasy NYC listings.

CRIME STATS
• Murder, felony assault, and property crime for the listing's neighborhood
• Rank across 197 NYC neighborhoods and comparison with the citywide rate
• Views for ambient risk, residents, area, raw count, and time window
• Bundled NYPD complaint data from NYC Open Data

PHOTO AREA ESTIMATES
• Analyze listing photos one at a time
• Label photos with room names to keep them organized
• Runs locally in Chrome, using WebGPU when available and CPU fallback otherwise
• Uses floor segmentation, depth, floor-plane fitting, and room-layout completion
• Downloads model data from Hugging Face once and caches it

PRIVACY AND NOTES
• No account, analytics, or tracking
• Listing photos are processed locally and are not uploaded
• NYC listing pages only
• Estimates are approximate. SleepEasy reports wall-to-wall floor area when the photo layout can be inferred; otherwise it reports visible floor area.
• Crime stats are informational and do not predict future safety.
• Independent project. Not affiliated with StreetEasy, Zillow Group, the NYPD, or the City of New York.

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
1. `01-crime-in-context.png` - crime module on a real listing
2. `02-room-sqft.png` - side panel room estimate
3. `03-how-it-works.png` - floor segmentation / computer vision

---

## Privacy practices tab

**Single purpose** (required)
```
SleepEasy augments StreetEasy NYC listing pages with neighborhood crime statistics and on-device photo-based floor-area estimates.
```

**Permission justifications**

| Permission | Justification to paste |
|---|---|
| `storage` | Saves the user's analyzed photos, room labels, and extension settings locally on their device. No data leaves the browser. |
| `sidePanel` | Provides the side-panel UI where users label listing photos and view square-footage estimates. |
| `activeTab` | Lets the extension read the current StreetEasy listing the user is viewing so it can show stats for that listing. |
| Host access to `streeteasy.com` (content scripts) | The extension only runs on StreetEasy listing pages, where it reads listing details and injects the crime module and photo controls. |
| Host access to `photos.zillowstatic.com` / `images.streeteasy.com` | Fetches the listing's own photos so the room-area model can analyze them on the user's device. The images are not stored or transmitted anywhere. |
| Host access to `huggingface.co` / `hf.co` | Downloads the open-source model files (data, not code) once, then caches them. No user data is sent. |

**Remote code:** No. All executable code (JavaScript and WebAssembly) ships in the package. Only model weight files (data) are fetched from Hugging Face and cached.

**Data usage disclosures:** the extension does **not** collect or use any category:
- Personally identifiable information - No
- Health information - No
- Financial and payment information - No
- Authentication information - No
- Personal communications - No
- Location - No (it reads a listing's location from the page to map it to a neighborhood, but does not collect or transmit any location data)
- Web history - No
- User activity - No
- Website content - No (listing photos are fetched and analyzed transiently on-device, not collected or transmitted)

**Certifications** (check all):
- I do not sell or transfer user data to third parties, outside of approved use cases - checked
- I do not use or transfer user data for purposes unrelated to my item's single purpose - checked
- I do not use or transfer user data to determine creditworthiness or for lending purposes - checked

**Privacy policy URL**
```
https://umqadir.github.io/streeteasy-enhanced-extension/privacy.html
```
