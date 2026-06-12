/**
 * Logic tests for the extension's client-side crime-stats code.
 *
 * Loads lib/geo-utils.js and lib/utils.js (content scripts) into a Node VM
 * with chrome/fetch stubs pointed at the real bundled data files, then checks
 * NTA lookup, measure math, ranks, and city reference values against
 * independent computations.
 *
 * Run: node scripts/test-extension-logic.mjs
 */

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const extDir = path.join(root, 'selfhost', 'extension');

// ── Sandbox with chrome + fetch stubs ──

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  chrome: { runtime: { getURL: (p) => path.join(extDir, p) } },
  fetch: async (filePath) => {
    const body = fs.readFileSync(filePath, 'utf8');
    return { ok: true, status: 200, json: async () => JSON.parse(body) };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const rel of ['lib/geo-utils.js', 'lib/utils.js']) {
  vm.runInContext(fs.readFileSync(path.join(extDir, rel), 'utf8'), sandbox, { filename: rel });
}

// ── Independent reference data ──

const stats = JSON.parse(fs.readFileSync(path.join(extDir, 'data/crime-stats.json'), 'utf8'));
const exposure = JSON.parse(fs.readFileSync(path.join(extDir, 'data/nta-exposure.json'), 'utf8'));

let failures = 0;
function check(name, cond, detail = '') {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures++;
    console.error(`FAIL  ${name}${detail ? ` — ${detail}` : ''}`);
  }
}
function approx(a, b, tol = 1e-6) {
  return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));
}

// ── Test: known coordinate → Yorkville (85 East End Ave) ──

const result = await vm.runInContext('fetchCrimeStats(40.773, -73.9456, "24m")', sandbox);

check('fetchCrimeStats returns a payload', !!result);
check('NTA is Upper East Side-Yorkville', result?.geography?.ntaName === 'Upper East Side-Yorkville', `got ${result?.geography?.ntaName}`);
check('borough is Manhattan', result?.geography?.borough === 'Manhattan');
check('dataThrough matches compiled data', result?.dataThrough === stats.dataThrough);

const ntaId = result.geography.ntaId;
const raw = stats.stats['24m'][ntaId];
const exp = exposure.exposures[ntaId];

for (const metric of ['murder', 'felonyAssault', 'propertyCrime']) {
  const m = result.metrics[metric];
  check(`${metric}: count matches compiled data`, m.measures.count.value === raw[metric].count);
  check(`${metric}: per100k matches compiled rate`, approx(m.measures.per100k.value, raw[metric].rate, 1e-9));

  const ambientPop = ((exp.population * 16) + (exp.jobsWac * 8)) / 24;
  const expectedAmbient = (raw[metric].count / ambientPop) * 100000;
  check(`${metric}: ambient math`, approx(m.measures.ambient.value, expectedAmbient, 1e-9));

  for (const measure of ['count', 'per100k', 'perSqMi', 'ambient']) {
    const mm = m.measures[measure];
    check(`${metric}/${measure}: rank in [1, total]`,
      mm.riskRank >= 1 && mm.riskRank <= mm.total && mm.total === 197,
      `rank=${mm.riskRank} total=${mm.total}`);
  }
}

// ── Test: rank consistency — recompute per100k rank for murder independently ──

{
  const values = Object.entries(stats.stats['24m'])
    .map(([id, m]) => ({ id, value: m.murder.rate }))
    .sort((a, b) => (a.value - b.value) || a.id.localeCompare(b.id));
  let rank = 0, last = null;
  const ranks = {};
  values.forEach((item, i) => {
    if (i === 0 || item.value !== last) { rank = i + 1; last = item.value; }
    ranks[item.id] = rank;
  });
  check('murder/per100k rank matches independent computation',
    result.metrics.murder.measures.per100k.riskRank === ranks[ntaId],
    `module=${result.metrics.murder.measures.per100k.riskRank} independent=${ranks[ntaId]}`);
}

// ── Test: city reference value (population-weighted) ──

{
  let totalCount = 0, totalPop = 0;
  for (const [id, m] of Object.entries(stats.stats['24m'])) {
    totalCount += m.felonyAssault.count;
    totalPop += exposure.exposures[id]?.population || 0;
  }
  const expectedCity = (totalCount / totalPop) * 100000;
  check('city per100k for felonyAssault is population-weighted total',
    approx(result.city.felonyAssault.per100k, expectedCity, 1e-9),
    `module=${result.city.felonyAssault.per100k} expected=${expectedCity}`);
  check('city value for count is null', result.city.felonyAssault.count === null);
}

// ── Test: formatting helpers ──

{
  const fmt = vm.runInContext('[formatRate(1414.83), formatRate(1.61), formatNumber(12345), formatCityMultiple(50, 100), formatCityMultiple(1200, 100), formatCityMultiple(5, 0)]', sandbox);
  check('formatRate >=1000 rounds to integer', fmt[0] === '1,415');
  check('formatRate small keeps 1 decimal', fmt[1] === '1.6');
  check('formatNumber adds commas', fmt[2] === '12,345');
  check('formatCityMultiple 0.5×', fmt[3] === '0.5×');
  check('formatCityMultiple 12×', fmt[4] === '12×');
  check('formatCityMultiple null on zero reference', fmt[5] === null);
}

// ── Test: a second coordinate in another borough (Williamsburg) ──

{
  const r2 = await vm.runInContext('fetchCrimeStats(40.7081, -73.9571, "12m")', sandbox);
  check('Williamsburg coordinate resolves to Brooklyn NTA',
    r2?.geography?.borough === 'Brooklyn', `got ${r2?.geography?.ntaName} (${r2?.geography?.borough})`);
}

// ── Test: out-of-NYC coordinate returns null ──

{
  const r3 = await vm.runInContext('fetchCrimeStats(42.65, -73.75, "24m")', sandbox);
  check('Albany coordinate returns null', r3 === null);
}

// ── Test: all five windows resolve ──

for (const w of ['3m', '6m', '12m', '24m', 'ytd']) {
  const r = await vm.runInContext(`fetchCrimeStats(40.773, -73.9456, "${w}")`, sandbox);
  check(`window ${w} resolves`, !!r?.metrics?.murder);
}

console.log(failures === 0 ? '\nAll checks passed.' : `\n${failures} check(s) FAILED.`);
process.exit(failures === 0 ? 0 : 1);
