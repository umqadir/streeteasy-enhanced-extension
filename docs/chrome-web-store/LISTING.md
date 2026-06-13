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
SleepEasy adds neighborhood crime statistics and room square-footage estimates to StreetEasy NYC listing pages. No accounts, no tracking, no data sent anywhere.

CRIME STATISTICS
• Murder, felony assault, and property crime for the listing's neighborhood
• Ranked across all 197 NYC neighborhoods, with comparison to the citywide rate
• Four measures (ambient risk index, per 100k residents, per square mile, raw counts) and three time windows (3, 12, 24 months)
• Built from NYPD complaint data via NYC Open Data; computed in your browser, no network calls

ROOM SQUARE FOOTAGE
• Group a listing's photos into rooms and estimate each room's floor area
• Floor segmentation, metric depth, and floor-plane fitting; multi-photo mode adds multi-view camera fusion
• Runs on a local backend you install and run yourself (one command), so photos stay on your machine

PRIVACY
• No accounts, no analytics, no tracking
• No data collected or transmitted
• NYC only

Open source (MIT). Independent project, not affiliated with or endorsed by StreetEasy, Zillow Group, the NYPD, or the City of New York. Crime statistics are informational; past incidence does not predict future safety.

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
