"""Are the three date fields' false-positive rates comparable with each other?

analyze_date_fields.py reports a complete-case FPR per field. Each of those is
computed over a *different set of rows*, because the fields are missing from
different records -- published-print is absent from 94 successful responses and
published-online from 173. Comparing 58.7% with 53.6% across different
subpopulations is not a comparison of the fields.

This script restricts to the rows where the fields being compared are all
present, so the difference is attributable to the field rather than to which
records happen to carry it. It also checks whether published-online means what
its name says.

Offline: reads only the enriched fixture. No network.
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'data'
FIXTURE = sorted(DATA.glob('preprint_hard_negatives_crossref_*.jsonl'))[-1]
FIELDS = ('published-print', 'published-online', 'issued')
TOLERANCE = 2

rows = [json.loads(s) for s in FIXTURE.read_text(encoding='utf-8').splitlines() if s]


def year(row, field):
    d = row['crossref_dates'][field]
    return d.get('year') if d.get('status') == 'present' else None


def flagged(row, field):
    y = year(row, field)
    return None if y is None else abs(y - row['arxiv_year']) > TOLERANCE


out = {'fixture': FIXTURE.name, 'rows': len(rows), 'tolerance_years': TOLERANCE}

# --- complete-case, as reported: each field on its own row set
out['complete_case'] = {}
for f in FIELDS:
    av = [r for r in rows if year(r, f) is not None]
    out['complete_case'][f] = {
        'n': len(av), 'flagged': sum(flagged(r, f) for r in av),
        'fpr': round(sum(flagged(r, f) for r in av) / len(av), 4) if av else None,
    }

# --- paired: only rows carrying every field, so the comparison is within-record
paired = [r for r in rows if all(year(r, f) is not None for f in FIELDS)]
out['paired'] = {'n': len(paired), 'fields': {}}
for f in FIELDS:
    k = sum(flagged(r, f) for r in paired)
    out['paired']['fields'][f] = {'flagged': k,
                                  'fpr': round(k / len(paired), 4) if paired else None}
out['paired']['by_delta'] = {
    str(d): {f: f"{sum(flagged(r, f) for r in paired if r['delta'] == d)}/"
                f"{sum(1 for r in paired if r['delta'] == d)}" for f in FIELDS}
    for d in sorted({r['delta'] for r in paired})}

# --- does published-online actually precede published-print?
both = [r for r in rows if year(r, 'published-print') and year(r, 'published-online')]
delta_op = [year(r, 'published-online') - year(r, 'published-print') for r in both]
later = [r for r in both if year(r, 'published-online') > year(r, 'published-print')]
# a false positive on online that print would not have raised
only_online = [r for r in later if flagged(r, 'published-online')
               and not flagged(r, 'published-print')]
out['online_vs_print'] = {
    'n_with_both': len(both),
    'online_earlier': sum(1 for d in delta_op if d < 0),
    'online_same_year': sum(1 for d in delta_op if d == 0),
    'online_later': sum(1 for d in delta_op if d > 0),
    'later_gap_histogram': dict(sorted(Counter(
        year(r, 'published-online') - year(r, 'published-print') for r in later).items())),
    'later_online_year_histogram': dict(sorted(Counter(
        year(r, 'published-online') for r in later).items())),
    'false_positives_only_online_raises': [
        {'case_id': r['case_id'], 'delta': r['delta'], 'arxiv_year': r['arxiv_year'],
         'print': year(r, 'published-print'), 'online': year(r, 'published-online')}
        for r in sorted(only_online, key=lambda r: r['case_id'])],
}

print(json.dumps(out, indent=2))
