#!/usr/bin/env node

/**
 * Tiny static server for local debug pages (avoids file:// CORS issues).
 *
 * Usage:
 *   node scripts/serve-debug-map.js
 *   PORT=4173 node scripts/serve-debug-map.js
 *
 * Then open:
 *   http://localhost:<port>/docs/data-explorer.html
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

const ROOT = path.join(__dirname, '..');
const PORT = Number(process.env.PORT || 4173);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml'
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, { 'cache-control': 'no-store', ...headers });
  res.end(body);
}

function safeJoin(base, requestPath) {
  const joined = path.join(base, requestPath);
  const normalizedBase = path.resolve(base) + path.sep;
  const normalizedJoined = path.resolve(joined);
  if (!normalizedJoined.startsWith(normalizedBase)) return null;
  return normalizedJoined;
}

function serveFile(res, absolutePath) {
  fs.stat(absolutePath, (err, st) => {
    if (err || !st.isFile()) return send(res, 404, 'Not found');
    const ext = path.extname(absolutePath).toLowerCase();
    const mime = MIME[ext] || 'application/octet-stream';
    fs.readFile(absolutePath, (err2, buf) => {
      if (err2) return send(res, 500, 'Read error');
      send(res, 200, buf, { 'content-type': mime });
    });
  });
}

const server = http.createServer((req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    let pathname = decodeURIComponent(url.pathname || '/');

    if (pathname === '/favicon.ico') {
      return send(res, 204, Buffer.from(''));
    }

    if (pathname === '/') {
      pathname = '/docs/data-explorer.html';
    }

    if (pathname.startsWith('/docs/')) {
      const filePath = safeJoin(path.join(ROOT, 'docs'), pathname.replace(/^\/docs\//, ''));
      if (!filePath) return send(res, 400, 'Bad request');
      return serveFile(res, filePath);
    }

    if (pathname.startsWith('/data/')) {
      const filePath = safeJoin(path.join(ROOT, 'selfhost-nc', 'extension', 'data'), pathname.replace(/^\/data\//, ''));
      if (!filePath) return send(res, 400, 'Bad request');
      return serveFile(res, filePath);
    }

    return send(res, 404, 'Not found');
  } catch (e) {
    return send(res, 500, `Server error: ${e.message}`);
  }
});

server.listen(PORT, '127.0.0.1', () => {
  // eslint-disable-next-line no-console
  console.log(`SleepEasy debug server: http://localhost:${PORT}/docs/data-explorer.html`);
});
