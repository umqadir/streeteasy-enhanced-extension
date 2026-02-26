#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="$ROOT_DIR/extension"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8787}"
TEST_IMAGE_URL="${TEST_IMAGE_URL:-https://images.pexels.com/photos/271624/pexels-photo-271624.jpeg?auto=compress&cs=tinysrgb&w=800}"
TEST_IMAGE_URL_2="${TEST_IMAGE_URL_2:-$TEST_IMAGE_URL}"
SESSION="${PLAYWRIGHT_SESSION:-sleepeasy-smoke-$$}"
TMP_DIR="$(mktemp -d -t sleepeasy-pw-XXXXXX)"
CONFIG_FILE="$TMP_DIR/playwright-cli-extension.json"
OPEN_LOG="$TMP_DIR/open.log"
SW_LOG="$TMP_DIR/sw.log"
TEST_LOG="$TMP_DIR/test.log"

cleanup() {
  playwright-cli -s="$SESSION" close >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
}

resolve_cli_package_dir() {
  local cli_bin cli_link cli_target
  cli_bin="$(command -v playwright-cli)"
  cli_link="$(readlink "$cli_bin" || true)"
  if [[ -n "$cli_link" ]]; then
    if [[ "$cli_link" = /* ]]; then
      cli_target="$cli_link"
    else
      cli_target="$(cd "$(dirname "$cli_bin")" && cd "$(dirname "$cli_link")" && pwd)/$(basename "$cli_link")"
    fi
  else
    cli_target="$cli_bin"
  fi
  cd "$(dirname "$cli_target")" && pwd
}

install_playwright_chromium() {
  local pkg_dir installer
  pkg_dir="$(resolve_cli_package_dir)"
  installer="$pkg_dir/node_modules/playwright/cli.js"
  if [[ ! -f "$installer" ]]; then
    echo "Cannot locate Playwright installer at: $installer"
    exit 1
  fi
  echo "Installing bundled Playwright Chromium runtime..."
  node "$installer" install chromium
}

echo "Using playwright-cli skill workflow: unpacked extension via persistent Chromium context."

require_cmd playwright-cli
require_cmd node
require_cmd curl
require_cmd rg

if ! curl -fsS "$BACKEND_URL/health" >"$TMP_DIR/backend_health.json"; then
  echo "Backend is not reachable at $BACKEND_URL"
  echo "Start it first: bash $ROOT_DIR/scripts/start_backend.sh"
  exit 1
fi

cat >"$CONFIG_FILE" <<JSON
{
  "browser": {
    "browserName": "chromium",
    "launchOptions": {
      "channel": "chromium",
      "args": [
        "--disable-extensions-except=$EXT_DIR",
        "--load-extension=$EXT_DIR"
      ]
    }
  },
  "allowUnrestrictedFileAccess": true,
  "outputMode": "stdout"
}
JSON

if ! playwright-cli -s="$SESSION" --config="$CONFIG_FILE" open about:blank --persistent >"$OPEN_LOG" 2>&1; then
  if rg -q 'Browser "chromium" is not installed' "$OPEN_LOG"; then
    install_playwright_chromium
    playwright-cli -s="$SESSION" --config="$CONFIG_FILE" open about:blank --persistent >"$OPEN_LOG" 2>&1
  else
    cat "$OPEN_LOG"
    exit 1
  fi
fi
cat "$OPEN_LOG"

playwright-cli -s="$SESSION" run-code "async page => {
  const sw = page.context().serviceWorkers()[0];
  if (!sw) return { id: null, swUrl: null };
  const swUrl = sw.url();
  const id = swUrl.replace(/^chrome-extension:\\/\\//, '').split('/')[0];
  return { id, swUrl };
}" >"$SW_LOG"

EXT_ID="$(sed -nE 's/.*\"id\":\"([a-z0-9]+)\".*/\1/p' "$SW_LOG" | head -n1)"
if [[ -z "$EXT_ID" ]]; then
  echo "Could not resolve extension id from service worker output."
  cat "$SW_LOG"
  exit 1
fi

SIDEPANEL_URL="chrome-extension://$EXT_ID/sidepanel/sidepanel.html"
echo "Resolved extension id: $EXT_ID"
echo "Navigating to sidepanel: $SIDEPANEL_URL"
playwright-cli -s="$SESSION" goto "$SIDEPANEL_URL" >/dev/null

BACKEND_URL_JS="$(printf '%s' "$BACKEND_URL" | sed 's/\\/\\\\/g; s/\"/\\"/g')"
TEST_IMAGE_URL_JS="$(printf '%s' "$TEST_IMAGE_URL" | sed 's/\\/\\\\/g; s/\"/\\"/g')"
TEST_IMAGE_URL_2_JS="$(printf '%s' "$TEST_IMAGE_URL_2" | sed 's/\\/\\\\/g; s/\"/\\"/g')"
TEST_CODE="$(cat <<JS
async page => {
  const backendUrl = "$BACKEND_URL_JS";
  const testImageUrl = "$TEST_IMAGE_URL_JS";
  const testImageUrl2 = "$TEST_IMAGE_URL_2_JS";
  const now = Date.now();

  await page.evaluate(async ({ nowTs, backendUrlValue, imageUrl1, imageUrl2 }) => {
    await chrome.storage.local.set({
      "area:listings": {
        "listing-1": {
          id: "listing-1",
          url: "https://example.com/listing-1",
          address: "123 Test St",
          createdAt: nowTs,
          updatedAt: nowTs
        }
      },
      "area:rooms:listing-1": [
        {
          id: "room-1",
          listingId: "listing-1",
          name: "Living Room",
          photoUrls: [imageUrl1, imageUrl2],
          estimatedSqft: null,
          pipeline: null,
          analyzedAt: null,
          outdated: false
        }
      ],
      "area:positions:listing-1": {
        [imageUrl1]: 1,
        [imageUrl2]: 2
      },
      "area:backend:config": {
        mode: "local",
        baseUrl: backendUrlValue,
        devicePolicy: "auto",
        analysisMode: "auto",
        noCudaPromptHandled: false
      }
    });
  }, { nowTs: now, backendUrlValue: backendUrl, imageUrl1: testImageUrl, imageUrl2: testImageUrl2 });

  await page.evaluate(async ({ backendUrlValue }) => {
    await chrome.runtime.sendMessage({
      type: "SET_BACKEND_CONFIG",
      config: {
        mode: "local",
        baseUrl: backendUrlValue,
        devicePolicy: "auto",
        analysisMode: "auto",
        noCudaPromptHandled: false
      }
    });
  }, { backendUrlValue: backendUrl });

  await page.reload();
  await page.waitForSelector("details.listing", { timeout: 10000 });

  await page.evaluate(() => {
    const details = document.querySelector("details.listing");
    if (details) {
      details.open = true;
      details.dispatchEvent(new Event("toggle", { bubbles: true }));
    }
  });
  await page.waitForTimeout(350);

  const health = await page.evaluate(() => chrome.runtime.sendMessage({ type: "GET_BACKEND_HEALTH" }));

  await page.evaluate(() => {
    window.__counts = { confirm: 0, alert: 0 };
    window.confirm = () => {
      window.__counts.confirm += 1;
      return false;
    };
    window.alert = () => {
      window.__counts.alert += 1;
    };
  });

  await page.evaluate(() => document.querySelector('[data-action="analyze"]')?.click());
  await page.waitForTimeout(1200);
  await page.evaluate(() => document.querySelector('[data-action="analyze"]')?.click());
  await page.waitForTimeout(1200);

  const afterDecline = await page.evaluate(async () => {
    const cfg = (await chrome.storage.local.get("area:backend:config"))["area:backend:config"];
    const st = await chrome.runtime.sendMessage({ type: "GET_LISTING_STATE", listingId: "listing-1" });
    return { cfg, state: st };
  });

  await page.evaluate(async () => {
    await chrome.runtime.sendMessage({
      type: "SET_BACKEND_CONFIG",
      config: { analysisMode: "single-image", noCudaPromptHandled: false }
    });
    window.__counts2 = { confirm: 0, alert: 0 };
    window.confirm = () => {
      window.__counts2.confirm += 1;
      return true;
    };
    window.alert = () => {
      window.__counts2.alert += 1;
    };
  });

  let capturedEstimateRequest = null;
  const onReq = req => {
    try {
      if (req.url().includes("/estimate/multi")) {
        capturedEstimateRequest = req.postDataJSON ? req.postDataJSON() : null;
      }
    } catch {}
  };
  page.context().on("request", onReq);

  const analyzeSingle = await page.evaluate(() =>
    chrome.runtime.sendMessage({ type: "ANALYZE_ROOM", listingId: "listing-1", roomId: "room-1" })
  );
  page.context().off("request", onReq);

  const afterSingle = await page.evaluate(async () => {
    const cfg = (await chrome.storage.local.get("area:backend:config"))["area:backend:config"];
    const st = await chrome.runtime.sendMessage({ type: "GET_LISTING_STATE", listingId: "listing-1" });
    const room = (st?.rooms || []).find(r => r.id === "room-1") || null;
    return { cfg, room };
  });

  const counts = await page.evaluate(() => ({
    confirm: window.__counts?.confirm || 0,
    alert: window.__counts?.alert || 0,
    confirm2: window.__counts2?.confirm || 0,
    alert2: window.__counts2?.alert || 0
  }));

  const pass = Boolean(
    health?.success &&
    health?.health?.ok &&
    health?.health?.mode === "local" &&
    health?.health?.capabilities?.cudaAvailable === false &&
    counts.confirm === 1 &&
    counts.alert >= 1 &&
    afterDecline?.cfg?.noCudaPromptHandled === true &&
    analyzeSingle?.success === true &&
    capturedEstimateRequest?.multiviewMethod === "single-image" &&
    afterSingle?.cfg?.analysisMode === "single-image" &&
    afterSingle?.room?.estimatedSqft !== null &&
    afterSingle?.room?.estimatedSqft !== undefined &&
    afterSingle?.room?.pipeline === "single" &&
    analyzeSingle?.result?.pipeline === "single"
  );

  return {
    pass,
    health,
    counts,
    analyzeSingle,
    capturedEstimateRequest,
    afterDeclineConfig: afterDecline?.cfg || null,
    afterSingleConfig: afterSingle?.cfg || null,
    roomAfterSingle: afterSingle?.room || null
  };
}
JS
)"

playwright-cli -s="$SESSION" run-code "$TEST_CODE" >"$TEST_LOG"
cat "$TEST_LOG"

if rg -q '"pass":true' "$TEST_LOG"; then
  echo "Smoke test PASS: UX is connected to backend and no-CUDA single-image fallback works."
else
  echo "Smoke test FAILED."
  exit 1
fi
