// Positive control for T11.
//
// T11 reports "year mismatch issues = 0" on the DOI path. That number is also
// what a broken harness returns, so on its own it is unfalsifiable. This runner
// sends the SAME fixtures down the free-text path (checkReference), where the
// upstream year comparison lives, and reports what fires. If the capability is
// real, the DOI-path zero is a routing consequence; if nothing fires here
// either, the instrument is broken and T11 proves nothing.
//
// It also classifies issues against all eight upstream year-message templates
// rather than the substring "year mismatch", because six of the eight never
// contain that word.
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
const hardFile = fs.readdirSync(path.join(root, 'benchmarks/data'))
  .filter(x => /^preprint_hard_negatives_\d{8}_\d{6}\.jsonl$/.test(x)).sort().at(-1);
const hard = read(path.join(root, 'benchmarks/data', hardFile));

// ---- frozen fixture, keyed by DOI and by title, serving both upstream routes
const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
const byDoi = new Map(), byTitle = new Map();
const put = (rec) => {
  byDoi.set(String(rec.doi).toLowerCase(), rec);
  if (!byTitle.has(norm(rec.title))) byTitle.set(norm(rec.title), rec);
};
for (const r of benchmark) if (r.category === 'real_control') put(r);
for (const r of hard) if (!byDoi.has(String(r.registered.doi).toLowerCase())) put(r.registered);

const crossrefItem = (rec) => {
  const authorsText = rec.authors || '';
  let families;
  try { families = JSON.parse(authorsText).map(a => String(a).split(',')[0].trim()); }
  catch { families = String(authorsText).split(/,\s*/).map(s => s.trim().split(/\s+/).at(-1)).filter(Boolean); }
  const year = Number(rec.year);
  return { DOI: String(rec.doi).toLowerCase(), title: [rec.title], author: families.map(family => ({ family })),
    'container-title': [rec.venue || ''], 'published-print': { 'date-parts': [[year]] },
    published: { 'date-parts': [[year]] }, issued: { 'date-parts': [[year]] },
    URL: `https://doi.org/${rec.doi}`, score: 1 };
};

const routes = {};
const missedQueries = [];
globalThis.fetch = async (input) => {
  const url = String(input);
  const u = new URL(url);
  if (u.hostname !== 'api.crossref.org') { routes[`BLOCKED:${u.hostname}`] = (routes[`BLOCKED:${u.hostname}`] || 0) + 1; return new Response('', { status: 503 }); }
  if (u.pathname.startsWith('/works/')) {
    routes['/works/{doi}'] = (routes['/works/{doi}'] || 0) + 1;
    const rec = byDoi.get(decodeURIComponent(u.pathname.slice('/works/'.length)).toLowerCase());
    return rec ? Response.json({ message: crossrefItem(rec) }) : new Response('', { status: 404 });
  }
  if (u.pathname === '/works' && u.searchParams.has('query')) {
    routes['/works?query'] = (routes['/works?query'] || 0) + 1;
    const rec = byTitle.get(norm(u.searchParams.get('query')));
    if (!rec) { routes['QUERY-MISS'] = (routes['QUERY-MISS'] || 0) + 1; missedQueries.push(u.searchParams.get('query')); }
    return Response.json({ message: { items: rec ? [crossrefItem(rec)] : [] } });
  }
  routes[`UNEXPECTED:${u.pathname}`] = (routes[`UNEXPECTED:${u.pathname}`] || 0) + 1;
  return new Response('', { status: 503 });
};

// ---- all eight upstream year-message templates (SearchService.ts:711-721, 890-900)
const YEAR_TEMPLATES = [
  [/^Year differs by \d+ year\(s\)/i,            'differs-by-N (preprint/published)'],
  [/^Note: expected .* CrossRef shows /i,         'online-first note'],
  [/^Year differs by 1:/i,                        'differs-by-1'],
  [/^Year Mismatch:/i,                            'YEAR MISMATCH'],
  [/^Note: year \d+ vs /i,                        'free-text preprint note'],
];
const classify = (issues) => issues.map(i => (YEAR_TEMPLATES.find(([re]) => re.test(i)) || [null, null])[1]).filter(Boolean);
const naiveSubstringRegex = (issues) => issues.some(i => /year.*mismatch|mismatch.*year/i.test(i));

const cases = [
  ...benchmark.map(r => ({ group: r.category, id: r.id, doi: r.doi, title: r.title,
      expected: { title: r.title, authors: r.authors, journal: r.venue, year: String(r.year) }, delta: null })),
  ...hard.map(r => ({ group: 'T10-hard-negative', id: r.case_id, doi: r.registered.doi, title: r.printed.title,
      expected: { title: r.printed.title, authors: r.printed.authors, journal: r.printed.venue, year: String(r.printed.year) }, delta: r.delta })),
];

const agg = {};
const examples = {};
for (const c of cases) {
  const doiRes  = await svc.checkWithFallback(c.title, c.expected, `doi:${c.doi}`);
  const textRes = await svc.checkReference(c.title, c.expected, c.title);
  for (const [route, res] of [['DOI', doiRes], ['free-text', textRes]]) {
    const key = `${c.group} | ${route}` + (c.delta ? ` | d${c.delta}` : '');
    const a = agg[key] ||= { n: 0, exists: 0, flagged_any_year: 0, naive_substring_regex_would_flag: 0, kinds: {} };
    a.n++; a.exists += Number(res.exists);
    const kinds = classify(res.issues || []);
    if (kinds.length) a.flagged_any_year++;
    if (naiveSubstringRegex(res.issues || [])) a.naive_substring_regex_would_flag++;
    for (const k of kinds) a.kinds[k] = (a.kinds[k] || 0) + 1;
    if (kinds.length && !examples[key]) examples[key] = { id: c.id, issues: res.issues };
  }
}
console.log(JSON.stringify({ fixture: hardFile, routes, missedSample: missedQueries.slice(0,5), missedCount: missedQueries.length, agg, examples }, null, 2));
