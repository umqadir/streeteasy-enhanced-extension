# Playwright CLI Extension Testing

This project tests the unpacked extension directly with `playwright-cli` by launching a persistent Chromium context with extension load flags.

## Why This Is the Canonical Path

- Playwright extension tests are run by loading an unpacked extension in a persistent Chromium context.
- Branded Chrome/Edge disable side-load flags, so use Playwright's `chromium` channel for extension automation.
- The Playwright MCP Bridge extension is only required for `playwright-cli open --extension` attach mode, not for loading your unpacked dev extension.

References:
- [Playwright Chrome extensions guide](https://playwright.dev/docs/chrome-extensions)
- [Chrome Developers: Testing Chrome Extensions with Playwright](https://developer.chrome.com/blog/chrome-extension-testing-playwright)
- [Playwright MCP Bridge README](https://github.com/microsoft/playwright-mcp/blob/main/packages/extension/README.md)

## Standard Workflow

1. Start backend:

```bash
cd selfhost-nc
bash scripts/start_backend.sh
```

2. Run extension smoke test:

```bash
cd selfhost-nc
bash scripts/smoke_playwright_no_cuda.sh
```

## What The Smoke Test Covers

- Extension service worker is loaded from unpacked `selfhost-nc/extension`
- Sidepanel renders and talks to backend (`GET_BACKEND_HEALTH`)
- No-CUDA auto mode prompts once and persists decline handling
- Manual `single-image` mode sends `multiviewMethod=single-image` for multi-photo room
- Room estimate is written back to extension storage

## Notes

- The script auto-installs Playwright `chromium` runtime for the bundled `playwright-cli` package when missing.
- `BACKEND_URL` can be overridden (default: `http://127.0.0.1:8787`).
- `TEST_IMAGE_URL` and `TEST_IMAGE_URL_2` can be overridden (default image is a public indoor photo URL).
