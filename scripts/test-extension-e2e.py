#!/usr/bin/env python3
"""
End-to-end test of the SleepEasy extension in Chromium.

StreetEasy's bot protection blocks automation on the live site, so the test
intercepts streeteasy.com at the network layer and serves a realistic fixture
listing (scripts/fixtures/listing-fixture.html). The content scripts inject
exactly as on the real site. A mock sqft backend runs on 127.0.0.1:8787.

Checks:
  1. Crime module renders in Shadow DOM with the correct NTA and values
  2. Measure + time-window selectors update the displayed numbers
  3. Photo hover overlay -> create room -> assign photo -> analyze (mock backend)
  4. Side panel shows the listing, room, and total sqft

Also captures screenshots into scripts/fixtures/out/.

Run: python3 scripts/test-extension-e2e.py
"""

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "selfhost" / "extension"
FIXTURE = (ROOT / "scripts" / "fixtures" / "listing-fixture.html").read_text()
OUT_DIR = ROOT / "scripts" / "fixtures" / "out"
OUT_DIR.mkdir(exist_ok=True)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL  {name}{f' — {detail}' if detail else ''}")


# ── Expected values straight from the bundled data ──

stats = json.loads((EXT_DIR / "data" / "crime-stats.json").read_text())
exposure = json.loads((EXT_DIR / "data" / "nta-exposure.json").read_text())

NTA_ID = "MN0803"  # Upper East Side-Yorkville (verified by logic tests)
nta_24m = stats["stats"]["24m"][NTA_ID]
exp = exposure["exposures"][NTA_ID]
ambient_pop = (exp["population"] * 16 + exp["jobsWac"] * 8) / 24


def fmt_rate(v):
    return f"{round(v):,}" if v >= 1000 else f"{v:.1f}"


EXPECTED_AMBIENT_FA = fmt_rate(nta_24m["felonyAssault"]["count"] / ambient_pop * 100000)
EXPECTED_PER100K_FA = fmt_rate(nta_24m["felonyAssault"]["rate"])
EXPECTED_PER100K_FA_12M = fmt_rate(stats["stats"]["12m"][NTA_ID]["felonyAssault"]["rate"])


# ── Mock sqft backend on 127.0.0.1:8787 ──

class MockBackend(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send({})

    def do_GET(self):
        if self.path == "/health":
            self._send({
                "ok": True,
                "capabilities": {"cudaAvailable": False},
                "device": {"policy": "cpu"},
                "analysisMode": "single-image",
            })
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/backend/config":
            self._send({"ok": True})
        elif self.path == "/estimate/single":
            self._send({"estimatedSqft": 232, "confidence": 0.8, "pipeline": "single", "method": "v2b_single"})
        elif self.path == "/estimate/multi":
            self._send({"estimatedSqft": 305, "confidence": 0.7, "pipeline": "single", "method": "v2b_single_fused"})
        else:
            self._send({"error": "not found"}, 404)


server = HTTPServer(("127.0.0.1", 8787), MockBackend)
threading.Thread(target=server.serve_forever, daemon=True).start()


# ── Placeholder listing photo ──

def make_photo(seed):
    img = Image.new("RGB", (800, 533), (235 - seed * 12, 230, 224))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 380, 800, 533), fill=(196 - seed * 10, 174, 148))   # floor
    d.rectangle((60, 80, 280, 360), fill=(250, 250, 250))               # window
    d.rectangle((400 + seed * 40, 200, 700, 380), fill=(120, 100, 90))  # furniture
    out = OUT_DIR / f"photo{seed}.jpg"
    img.save(out, "JPEG")
    return out.read_bytes()


PHOTOS = {f"fixturehash{i}": make_photo(i) for i in (1, 2, 3)}


# ── Browser ──

with sync_playwright() as p:
    import glob
    chromium = sorted(glob.glob(
        str(Path.home() / "Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
    ))[-1]
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(OUT_DIR / "profile"),
        executable_path=chromium,
        headless=False,
        viewport={"width": 1440, "height": 1000},
        args=[
            f"--disable-extensions-except={EXT_DIR}",
            f"--load-extension={EXT_DIR}",
        ],
    )

    def route_streeteasy(route):
        url = route.request.url
        if "favicon" in url:
            route.fulfill(status=204)
        else:
            route.fulfill(status=200, content_type="text/html", body=FIXTURE)

    def route_photos(route):
        m = re.search(r"/fp/(fixturehash\d)", route.request.url)
        body = PHOTOS.get(m.group(1) if m else "", PHOTOS["fixturehash1"])
        route.fulfill(status=200, content_type="image/jpeg", body=body)

    page = ctx.new_page()
    page.route("https://streeteasy.com/**", route_streeteasy)
    page.route("https://photos.zillowstatic.com/**", route_photos)

    page.goto("https://streeteasy.com/rental/test-fixture-10g")

    # ── 1. Crime module renders ──
    page.wait_for_selector("#sleepeasy-module", timeout=15000)
    module = page.locator("#sleepeasy-module")

    def shadow_text():
        return page.evaluate(
            "document.getElementById('sleepeasy-module').shadowRoot.textContent"
        )

    page.wait_for_function(
        "() => /Yorkville/.test(document.getElementById('sleepeasy-module')?.shadowRoot?.textContent || '')",
        timeout=15000,
    )
    text = shadow_text()
    check("module shows NTA name", "Upper East Side-Yorkville, Manhattan" in text, text[:200])
    check("module shows all three metrics",
          all(k in text for k in ("Felony assault", "Property crime", "Murder")))
    check("module shows ambient felony-assault value", EXPECTED_AMBIENT_FA in text,
          f"expected {EXPECTED_AMBIENT_FA}")
    check("module shows rank format", re.search(r"#\d+ of 197", text) is not None)
    check("module shows NYC multiplier", re.search(r"\d× NYC rate", text) is not None)
    check("module footer shows data date", "through" in text and "NYC Open Data" in text)

    page.screenshot(path=str(OUT_DIR / "listing-with-module.png"))
    module.screenshot(path=str(OUT_DIR / "crime-module.png"))

    # ── 2. Measure + window selectors ──
    page.evaluate("""() => {
      const root = document.getElementById('sleepeasy-module').shadowRoot;
      const sel = root.querySelector('.se-measure-select');
      sel.value = 'per100k';
      sel.dispatchEvent(new Event('change'));
    }""")
    page.wait_for_timeout(300)
    text = shadow_text()
    check("per100k measure shows compiled rate", EXPECTED_PER100K_FA in text,
          f"expected {EXPECTED_PER100K_FA} in {text[:300]}")

    page.evaluate("""() => {
      const root = document.getElementById('sleepeasy-module').shadowRoot;
      const sel = root.querySelector('.se-window-select');
      sel.value = '12m';
      sel.dispatchEvent(new Event('change'));
    }""")
    page.wait_for_timeout(500)
    text = shadow_text()
    check("12m window updates values", EXPECTED_PER100K_FA_12M in text,
          f"expected {EXPECTED_PER100K_FA_12M}")

    # reset to defaults for screenshots
    page.evaluate("""() => {
      const root = document.getElementById('sleepeasy-module').shadowRoot;
      const sel = root.querySelector('.se-window-select');
      sel.value = '24m';
      sel.dispatchEvent(new Event('change'));
    }""")
    page.wait_for_timeout(500)

    # ── 3. Photo overlay -> room -> analyze ──
    photo = page.locator('button[aria-label="photo 1"] img')
    photo.hover()
    page.wait_for_selector(".sleepsy-overlay", timeout=5000)
    check("hover overlay appears", page.locator(".sleepsy-overlay").count() == 1)

    page.locator(".sleepsy-room-btn").click()
    page.wait_for_selector(".sleepsy-dropdown", timeout=5000)
    page.locator(".sleepsy-dd-create-input").fill("Living Room")
    page.locator(".sleepsy-dd-create-btn").click()
    page.wait_for_timeout(600)

    photo.hover()
    page.wait_for_selector(".sleepsy-overlay", timeout=5000)
    overlay_text = page.locator(".sleepsy-overlay").text_content()
    check("photo assigned to room", "Living Room" in overlay_text, overlay_text)

    page.locator(".sleepsy-sqft-btn").click()
    page.wait_for_function(
        "() => document.querySelector('.sleepsy-overlay')?.textContent.includes('232')",
        timeout=10000,
    )
    check("analyze returns mock sqft", True)
    page.screenshot(path=str(OUT_DIR / "photo-overlay.png"))

    # ── 4. Side panel state ──
    ext_id = None
    for sw in ctx.service_workers:
        m = re.match(r"chrome-extension://([a-z]+)/", sw.url)
        if m:
            ext_id = m.group(1)
    check("found extension service worker", ext_id is not None)

    panel = ctx.new_page()
    panel.set_viewport_size({"width": 380, "height": 700})
    panel.goto(f"chrome-extension://{ext_id}/sidepanel/sidepanel.html")
    panel.wait_for_selector("details.listing", timeout=10000)
    panel.locator("details.listing").first.locator("summary").click()
    panel.wait_for_timeout(500)
    panel_text = panel.locator("#app").text_content()
    check("side panel lists the fixture listing", "85 East End Avenue" in panel_text, panel_text[:200])
    room_name = panel.locator(".room-name").first.input_value()
    check("side panel shows the room", room_name == "Living Room", room_name)
    check("side panel shows total sqft", "232" in panel_text)
    panel.screenshot(path=str(OUT_DIR / "sidepanel.png"))

    ctx.close()

server.shutdown()

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
    sys.exit(1)
print("All e2e checks passed.")
