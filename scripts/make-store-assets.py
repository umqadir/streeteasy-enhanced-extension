#!/usr/bin/env python3
"""
Build documentation images and Chrome Web Store screenshots for SleepEasy.

1. Renders the v2 crime module at the listing-column width and composites it
   into the archived real-listing screenshot (replacing the v1 module pixels)
   -> docs/images/crime-module-in-context.png and store screenshot 01.
2. Composes a 1280x800 side-panel slide -> store screenshot 02.
   (Screenshot 03, the CV explainer, is version-independent and kept as is.)

Requires scripts/fixtures/out/sidepanel.png from test-extension-e2e.py and
the v1 in-context screenshot in git history.

Run: python3 scripts/make-store-assets.py
"""

import glob
import subprocess
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "fixtures" / "out"
STORE = ROOT / "docs" / "chrome-web-store" / "screenshots"

CHROMIUM = sorted(glob.glob(
    str(Path.home() / "Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
))[-1]

# The archived v1 screenshot of a real listing (base for the composite)
BASE_COMMIT_PATH = "docs/images/crime-module-in-context.png"
base_png = subprocess.run(
    ["git", "-C", str(ROOT), "show", f"bcc82c3~1:{BASE_COMMIT_PATH}"],
    capture_output=True, check=True,
).stdout
(OUT / "base-context.png").write_bytes(base_png)

MODULE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  body { margin: 0; background: #fff; }
  .wrap { width: 425px; }
""" + (ROOT / "launch" / "styles.css").read_text().split("/* ── Module replica ── */")[1].split("/* ── Chapters ── */")[0]
MODULE_HTML = MODULE_HTML.replace("var(--hairline)", "#e5e7eb").replace("var(--card)", "#ffffff") \
    .replace("var(--green)", "#79b292").replace("var(--sand)", "#ddc287").replace("var(--clay)", "#d99087")
MODULE_HTML += """
  .module { border: 0; border-radius: 0; box-shadow: none; padding: 12px 0 8px;
            border-top: 1px solid #e5e7eb; border-bottom: 1px solid #e5e7eb;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
</style></head><body><div class="wrap">
""" + """
<div class="module">
  <div class="m-head">
    <div>
      <div class="m-label">Crime</div>
      <div class="m-place">Upper East Side-Yorkville, Manhattan</div>
    </div>
    <span class="m-info">i</span>
  </div>
  <div class="m-scale"><span>lower crime</span><span>higher crime</span></div>
  <div class="m-metric">
    <div class="m-line"><span class="m-name">Felony assault</span><span class="m-value"><strong>247.3</strong> <em>/ 100k present</em></span></div>
    <div class="m-line m-sub"><span class="m-track"><span class="m-dot" style="left:9.7%"></span></span><span class="m-rank">#20 of 197 · 0.3× NYC rate</span></div>
  </div>
  <div class="m-metric">
    <div class="m-line"><span class="m-name">Property crime</span><span class="m-value"><strong>1,352</strong> <em>/ 100k present</em></span></div>
    <div class="m-line m-sub"><span class="m-track"><span class="m-dot" style="left:26.5%"></span></span><span class="m-rank">#53 of 197 · 0.7× NYC rate</span></div>
  </div>
  <div class="m-metric">
    <div class="m-line"><span class="m-name">Murder</span><span class="m-value"><strong>1.6</strong> <em>/ 100k present</em></span></div>
    <div class="m-line m-sub"><span class="m-track"><span class="m-dot" style="left:26.0%"></span></span><span class="m-rank">#52 of 197 · 0.2× NYC rate</span></div>
  </div>
  <div class="m-controls">
    <span class="m-select">Ambient risk index ▾</span>
    <span class="m-select">Last 24 months ▾</span>
  </div>
  <div class="m-foot">NYPD complaints via NYC Open Data · through Mar 31, 2026</div>
</div>
</div></body></html>
"""

SLIDE_02_HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500&family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  body { margin: 0; width: 1280px; height: 800px; background:
         radial-gradient(700px 400px at 30% -10%, rgba(14,23,38,0.05), transparent 70%), #faf8f3;
         display: flex; align-items: center; font-family: "Instrument Sans", sans-serif; overflow: hidden; }
  .text { flex: 1; padding: 0 30px 0 84px; }
  h1 { font-family: "Fraunces", serif; font-weight: 500; font-size: 52px; line-height: 1.08;
       letter-spacing: -0.015em; color: #16171d; margin: 0 0 22px; }
  p { font-size: 20px; line-height: 1.55; color: #6f6a5e; max-width: 26em; margin: 0; }
  .panel { flex: 0 0 auto; margin-right: 84px; border-radius: 14px; overflow: hidden;
           border: 1px solid #e6e1d5; box-shadow: 0 30px 70px -30px rgba(14,23,38,0.35); }
  .panel img { display: block; width: 380px; }
</style></head><body>
  <div class="text">
    <h1>Group photos into rooms. Get square footage.</h1>
    <p>A computer-vision pipeline measures each room's floor from the listing's own
       photos — on a local backend, so nothing ever leaves your machine.</p>
  </div>
  <div class="panel"><img src="__SIDEPANEL__"></div>
</body></html>
"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROMIUM, headless=True)

    # 1) Module at listing-column width
    pg = b.new_page(viewport={"width": 500, "height": 700}, device_scale_factor=1)
    (OUT / "module-425.html").write_text(MODULE_HTML)
    pg.goto(f"file://{OUT / 'module-425.html'}")
    pg.wait_for_timeout(400)
    pg.locator(".module").screenshot(path=str(OUT / "module-425.png"))

    # 2) Side-panel slide (1280x800)
    slide = SLIDE_02_HTML.replace("__SIDEPANEL__", str(OUT / "sidepanel.png"))
    (OUT / "slide-02.html").write_text(slide)
    pg2 = b.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
    pg2.goto(f"file://{OUT / 'slide-02.html'}")
    pg2.wait_for_timeout(1200)
    pg2.screenshot(path=str(STORE / "02-room-sqft.png"))

    b.close()

# 3) Composite the module into the real-listing screenshot
base = Image.open(OUT / "base-context.png").convert("RGB")
module = Image.open(OUT / "module-425.png").convert("RGB")

X, Y = 815, 503
# Clear the old module plus the area the taller v2 module needs
clear_bottom = min(Y + module.height + 12, base.height)
base.paste((255, 255, 255), (X - 5, Y - 6, 1245, clear_bottom))
base.paste(module, (X, Y))

base.save(ROOT / "docs" / "images" / "crime-module-in-context.png")
base.resize((1280, 800), Image.LANCZOS).save(STORE / "01-crime-in-context.png")

print("module size:", module.size)
print("wrote: docs/images/crime-module-in-context.png, store 01, store 02")
