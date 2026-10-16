import gzip, json, re, random, collections, datetime
from pathlib import Path

SRC = '/tmp/arxiv-abstracts-2021.jsonl.gz'
SEED = 20261013
YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s,;]+', re.I)

def arxiv_year(raw):
    s = re.sub(r'v\d+$', '', raw)
    m = re.match(r'^(\d{2})(\d{2})\.\d+', s)
    if m:
        yy, mm = map(int, m.groups())
        if not 1 <= mm <= 12: return None
        return (1900 + yy if yy >= 91 else 2000 + yy)
    m = re.match(r'^[a-z-]+/(\d{2})(\d{2})\d+', s, re.I)
    if m:
        yy, mm = map(int, m.groups())
        if not 1 <= mm <= 12: return None
        return 1900 + yy if yy >= 91 else 2000 + yy
    return None

def cat_of(s):
    cats = s if isinstance(s, list) else str(s).split()
    if not cats: return None
    c = cats[0].lower()
    if c.startswith(('hep-', 'astro-ph', 'cond-mat', 'gr-qc', 'nucl-', 'physics.', 'quant-ph', 'nlin.')): return 'physics'
    if c.startswith(('cs.', 'eess.')) or c in ('cs', 'eess'): return 'cs'
    if c.startswith(('math.', 'math-ph')) or c in ('math', 'solv-int'): return 'math'
    if c.startswith(('stat.', 'q-fin.', 'econ.')) or c in ('stat', 'q-fin', 'econ'): return 'stat'
    if c.startswith('q-bio.') or c == 'q-bio': return 'q-bio'
    return None

stats = collections.Counter()
fail = collections.Counter()
reservoir = collections.defaultdict(list)
rng = random.Random(SEED)
seen = collections.Counter()
sample = []
with gzip.open(SRC, 'rt', encoding='utf-8') as f:
  for line in f:
    stats['scanned'] += 1
    try: d = json.loads(line)
    except Exception: stats['bad_json'] += 1; continue
    aid = str(d.get('id') or '')
    ay = arxiv_year(aid)
    if ay is None: fail['arxiv_id_unparsed'] += 1; continue
    jr = str(d.get('journal-ref') or '').strip()
    if not jr: fail['journal_ref_missing'] += 1; continue
    years = sorted(set(map(int, YEAR_RE.findall(jr))))
    if not years: fail['journal_year_missing'] += 1; continue
    if len(years) != 1: fail['journal_year_ambiguous'] += 1; continue
    py = years[0]
    if py < ay: fail['publication_before_preprint'] += 1; continue
    delta = py - ay
    if not 1 <= delta <= 5: fail['delta_outside_1_5'] += 1; continue
    stats['year_valid_delta_1_5'] += 1
    dois = DOI_RE.findall(str(d.get('doi') or ''))
    if not dois: fail['doi_missing'] += 1; continue
    if len(dois) != 1: fail['doi_ambiguous_multiple'] += 1; continue
    stats['doi_and_year_valid'] += 1
    cat = cat_of(d.get('categories') or '')
    if cat is None: fail['category_unmapped'] += 1; continue
    stats['eligible'] += 1
    key = (delta, cat)
    seen[key] += 1
    item = {'id':aid,'arxiv_year':ay,'pub_year':py,'delta':delta,'cat':cat,'journal_ref':jr,'doi':dois[0].rstrip('.'),'title':str(d.get('title') or '').strip().replace('\n',' '),'authors':str(d.get('authors') or '').strip().replace('\n',' '),'categories':d.get('categories') or []}
    # reservoir per delta/category, cap 1000 each
    cap = 1000
    if len(reservoir[key]) < cap: reservoir[key].append(item)
    else:
      j = rng.randrange(seen[key])
      if j < cap: reservoir[key][j] = item

# Ensure every delta has at least 20 and every required category appears (prefer >=20 per category).
selected=[]
for delta in range(1,6):
  for cat in ('physics','cs','math','stat','q-bio'):
    vals=reservoir[(delta,cat)]
    rng.shuffle(vals)
    selected.extend(vals[:20])
sample=[{'id':x['id'],'journal-ref':x['journal_ref'],'preprint_year':x['arxiv_year'],'publication_year':x['pub_year']} for x in selected[:20]]

stamp = datetime.datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
out=[]
for i,x in enumerate(selected,1):
  # This public snapshot carries one journal-ref year, not raw Crossref date parts.
  # Preserve that limitation explicitly; T12 must not treat these as observed dates.
  row={'case_id':f'T10-{i:04d}','source':'gfissore/arxiv-abstracts-2021','expected_risk':'LOW','category':x['cat'],'arxiv_id':x['id'],'arxiv_year':x['arxiv_year'],'delta':x['delta'],'printed':{'title':x['title'],'authors':x['authors'],'venue':x['journal_ref'],'doi':x['doi'],'year':x['arxiv_year']},'registered':{'doi':x['doi'],'title':x['title'],'authors':x['authors'],'venue':x['journal_ref'],'year':x['pub_year']},'crossref_dates':{'published-print':None,'published-online':None,'issued':None,'posted':None},'date_evidence':'publication year parsed from journal-ref; raw Crossref date-parts unavailable in source snapshot'}
  out.append(json.dumps(row,ensure_ascii=False,separators=(',',':')))
fixture = Path(__file__).parent / f'preprint_hard_negatives_{stamp}.jsonl'
print(json.dumps({'stamp':stamp,'fixture':fixture.name,'seed':SEED,'stats':dict(stats),'failures':dict(fail),'counts_by_delta':dict(collections.Counter(x['delta'] for x in selected)),'counts_by_category':dict(collections.Counter(x['cat'] for x in selected)),'available_by_delta_category':{f'{d}/{c}':seen[(d,c)] for d in range(1,6) for c in ('physics','cs','math','stat','q-bio')},'audit_sample':sample},ensure_ascii=False))
print('---FIXTURE---')
print('\n'.join(out))
