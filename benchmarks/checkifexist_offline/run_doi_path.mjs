import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.cwd();
// The upstream checker is NOT vendored here: it is third-party code under its
// own licence, and the whole point of this harness is that it runs unmodified.
// Clone it at the pinned revision (README.md has the command), or point
// CHECKIFEXIST_CLONE at an existing clone (tried first; skipped if absent).
const CLONE_CANDIDATES = [
  process.env.CHECKIFEXIST_CLONE,
  path.join(root, 'vendor/References-Validation'),
  path.join(root, '.codex/References-Validation'),
].filter(Boolean);
const clone = CLONE_CANDIDATES.find(p => fs.existsSync(path.join(p, 'src/services/SearchService.ts')));
if (!clone) {
  console.error('CheckIfExist clone not found. Looked in:\n  ' + CLONE_CANDIDATES.join('\n  ') +
                '\nSee benchmarks/checkifexist_offline/README.md');
  process.exit(2);
}
const svc = await import(pathToFileURL(path.join(clone, 'src/services/SearchService.ts')).href);
const read = (p) => fs.readFileSync(p, 'utf8').trim().split(/\r?\n/).map(JSON.parse);
const benchmark = read(path.join(root, 'benchmarks/data/idchim_year_only.jsonl'));
const hardFile = fs.readdirSync(path.join(root, 'benchmarks/data')).filter(x => /^preprint_hard_negatives_\d{8}_\d{6}\.jsonl$/.test(x)).sort().at(-1);
const hard = read(path.join(root, 'benchmarks/data', hardFile));
const registeredByDoi = new Map();
for (const row of benchmark) if (row.category === 'real_control') registeredByDoi.set(row.doi.toLowerCase(), row);
for (const row of hard) if (!registeredByDoi.has(row.registered.doi.toLowerCase())) registeredByDoi.set(row.registered.doi.toLowerCase(), row.registered);

let fetchCalls = 0;
const blockedRoutes = {};
globalThis.fetch = async (input) => {
  const url = String(input);
  if (!url.startsWith('https://api.crossref.org/works/')) {
    const route = new URL(url).hostname;
    blockedRoutes[route] = (blockedRoutes[route] || 0) + 1;
    return new Response('', { status: 503 });
  }
  fetchCalls++;
  const doi = decodeURIComponent(url.slice('https://api.crossref.org/works/'.length)).toLowerCase();
  const row = registeredByDoi.get(doi);
  if (!row) return new Response('', { status: 404 });
  const year = Number(row.year || row.registered?.year);
  const title = row.title || row.registered?.title;
  const authorsText = row.authors || row.registered?.authors || '';
  const families = authorsText.split(/,\s*(?=[A-Z])/).map(s => s.trim().split(/\s+/).at(-1)).filter(Boolean);
  const venue = row.venue || row.registered?.venue || '';
  return Response.json({ message: { DOI: doi, title: [title], author: families.map(family => ({ family })), 'container-title': [venue], 'published-print': { 'date-parts': [[year]] }, published: { 'date-parts': [[year]] }, issued: { 'date-parts': [[year]] }, URL: `https://doi.org/${doi}`, score: 1 } });
};

const inputs = [
  ...benchmark.map(row => ({ group: row.category, row, expectedYear: String(row.year), expected: { title: row.title, authors: row.authors, journal: row.venue, year: String(row.year) } })),
  ...hard.map(row => ({ group: 'T10-hard-negative', row, expectedYear: String(row.printed.year), expected: { title: row.printed.title, authors: row.printed.authors, journal: row.printed.venue, year: String(row.printed.year) } }))
];
const outputs = [];
for (const x of inputs) {
  const doi = x.row.doi || x.row.registered.doi;
  const title = x.row.title || x.row.printed.title;
  const result = await svc.checkWithFallback(title, x.expected, `doi:${doi}`);
  outputs.push({ group: x.group, id: x.row.id || x.row.case_id, expected_year: x.expectedYear, result_year: result.year, exists: result.exists, source: result.source, confidence: result.matchConfidence, issues: result.issues, has_year_mismatch_issue: result.issues.some(i => /year.*mismatch|mismatch.*year/i.test(i)) });
}
const groups = {};
for (const x of outputs) {
  const g = groups[x.group] ||= { n: 0, exists: 0, year_issue: 0, years: {}, confidence: {} };
  g.n++; g.exists += Number(x.exists); g.year_issue += Number(x.has_year_mismatch_issue);
  g.years[`${x.expected_year}->${x.result_year}`] = (g.years[`${x.expected_year}->${x.result_year}`] || 0) + 1;
  g.confidence[x.confidence] = (g.confidence[x.confidence] || 0) + 1;
}
console.log(JSON.stringify({ total: outputs.length, fetchCalls, blockedRoutes, groups, anomalies: outputs.filter(x => x.source !== 'CrossRef' || x.confidence !== 100 || x.has_year_mismatch_issue) }, null, 2));
