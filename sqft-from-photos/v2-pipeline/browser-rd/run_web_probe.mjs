#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function parseArgs(argv) {
  const out = {
    root: path.resolve(__dirname, "web"),
    modelPath: "../models/model.onnx",
    provider: "webgpu",
    size: 320,
    runs: 2,
    warmup: 1,
    numTokens: 1200,
    outDir: null,
    port: 8789,
    timeoutMs: 240000,
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    const b = argv[i + 1];
    if (a === "--root" && b) out.root = path.resolve(b), i++;
    else if (a === "--model-path" && b) out.modelPath = b, i++;
    else if (a === "--provider" && b) out.provider = b, i++;
    else if (a === "--size" && b) out.size = Number.parseInt(b, 10), i++;
    else if (a === "--runs" && b) out.runs = Number.parseInt(b, 10), i++;
    else if (a === "--warmup" && b) out.warmup = Number.parseInt(b, 10), i++;
    else if (a === "--num-tokens" && b) out.numTokens = Number.parseInt(b, 10), i++;
    else if (a === "--out-dir" && b) out.outDir = path.resolve(b), i++;
    else if (a === "--port" && b) out.port = Number.parseInt(b, 10), i++;
    else if (a === "--timeout-ms" && b) out.timeoutMs = Number.parseInt(b, 10), i++;
  }
  if (!out.outDir) throw new Error("--out-dir is required");
  return out;
}

function mimeType(filePath) {
  if (filePath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filePath.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (filePath.endsWith(".css")) return "text/css; charset=utf-8";
  if (filePath.endsWith(".json")) return "application/json; charset=utf-8";
  if (filePath.endsWith(".onnx")) return "application/octet-stream";
  return "application/octet-stream";
}

function startStaticServer(root, port) {
  const srv = http.createServer((req, res) => {
    try {
      const raw = (req.url || "/").split("?")[0];
      const rel = decodeURIComponent(raw === "/" ? "/probe.html" : raw);
      const safeRel = rel.replace(/^\/+/, "");
      const full = path.resolve(root, safeRel);
      if (!full.startsWith(path.resolve(root))) {
        res.writeHead(403).end("forbidden");
        return;
      }
      if (!fs.existsSync(full) || fs.statSync(full).isDirectory()) {
        res.writeHead(404).end("not found");
        return;
      }
      res.writeHead(200, { "content-type": mimeType(full) });
      fs.createReadStream(full).pipe(res);
    } catch (e) {
      res.writeHead(500).end(String(e));
    }
  });
  return new Promise((resolve, reject) => {
    srv.once("error", reject);
    srv.listen(port, "127.0.0.1", () => resolve(srv));
  });
}

function findChromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ].filter(Boolean);
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

async function run() {
  const cfg = parseArgs(process.argv);
  fs.mkdirSync(cfg.outDir, { recursive: true });

  const server = await startStaticServer(cfg.root, cfg.port);
  let browser = null;
  let result = null;
  let consoleLogs = [];

  try {
    const chromePath = findChromePath();
    const launchOpts = {
      headless: true,
      args: [
        "--enable-unsafe-webgpu",
        "--ignore-gpu-blocklist",
        "--use-angle=metal",
        "--disable-dev-shm-usage",
      ],
    };
    if (chromePath) launchOpts.executablePath = chromePath;
    browser = await chromium.launch(launchOpts);
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    page.on("console", (msg) => {
      consoleLogs.push({ type: msg.type(), text: msg.text() });
    });

    const url =
      `http://127.0.0.1:${cfg.port}/probe.html` +
      `?model=${encodeURIComponent(cfg.modelPath)}` +
      `&provider=${encodeURIComponent(cfg.provider)}` +
      `&size=${encodeURIComponent(String(cfg.size))}` +
      `&runs=${encodeURIComponent(String(cfg.runs))}` +
      `&warmup=${encodeURIComponent(String(cfg.warmup))}` +
      `&num_tokens=${encodeURIComponent(String(cfg.numTokens))}`;

    await page.goto(url, { waitUntil: "networkidle", timeout: cfg.timeoutMs });

    await page.waitForFunction(
      () => window.__probeResult !== undefined || window.__probeError !== undefined,
      { timeout: cfg.timeoutMs }
    );

    const probe = await page.evaluate(() => window.__probeResult || window.__probeError);
    result = {
      ok: !probe.error,
      provider: cfg.provider,
      probe,
      url,
    };
    await page.screenshot({ path: path.join(cfg.outDir, `probe_${cfg.provider}.png`), fullPage: true });
  } catch (err) {
    result = {
      ok: false,
      provider: cfg.provider,
      error: String(err && err.stack ? err.stack : err),
    };
  } finally {
    try {
      if (browser) await browser.close();
    } catch {}
    await new Promise((resolve) => server.close(resolve));
  }

  const out = {
    ...result,
    console: consoleLogs,
  };
  fs.writeFileSync(
    path.join(cfg.outDir, `web_probe_${cfg.provider}.json`),
    JSON.stringify(out, null, 2) + "\n",
    "utf-8"
  );
  process.stdout.write(JSON.stringify({ ok: out.ok, provider: cfg.provider }) + "\n");
}

run().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err) + "\n");
  process.exit(1);
});
