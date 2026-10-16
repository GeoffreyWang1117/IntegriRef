"""Run Binara-Sachin/doi-reference-validator unmodified and offline.

What this measures: this checker does compare the year on its DOI route, by
exact substring, with no tolerance at all -- so it catches every year chimera
and flags most legitimate preprint-to-publication references too.

Only `requests.get` is stubbed and `time.sleep` neutralised. The sleep is a
politeness delay with no decision logic behind it; neutralising a sleep that
sits in front of a circuit breaker would undercount network dependence, and
this tool has none.

  python benchmarks/independent_checkers/run_binara.py
"""
import collections
import importlib.util
import io
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H                                              # noqa: E402

H.install_guard()
ENTRY = H.find_clone('binara', 'BINARA_CLONE')
spec = importlib.util.spec_from_file_location('bsv', ENTRY)
bsv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsv)


class Resp:
    def __init__(self, code, payload=None):
        self.status_code, self._p = code, payload

    def json(self):
        return self._p


def fake_get(url, headers=None, **kw):
    doi = urllib.parse.unquote(url.rsplit('/works/', 1)[-1]).lower()
    return Resp(200, {'message': H.REG[doi]}) if doi in H.REG else Resp(404)


bsv.requests.get = fake_get
bsv.time.sleep = lambda *a, **k: None

# Verdicts are read off the table the tool builds, so no upstream line changes.
ROWS = []
from rich.table import Table                                     # noqa: E402
_orig_add_row = Table.add_row


def _spy(self, *cells, **kw):
    if len(cells) == 7 and all(isinstance(c, str) for c in cells):
        ROWS.append(cells)      # the validator's table; rich's own progress
                                # rendering calls add_row with other arity
    return _orig_add_row(self, *cells, **kw)


Table.add_row = _spy


def report(cases, label, expect):
    ROWS.clear()
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        bsv.validate_references([line for _, _, line in cases])
    finally:
        sys.stdout = old
    assert len(ROWS) == len(cases), f'{len(ROWS)} rows for {len(cases)} cases'
    by = collections.defaultdict(collections.Counter)
    for (_, delta, _), cells in zip(cases, ROWS):
        status, title, year = cells[2], cells[3], cells[4]
        resolved = 'Valid' in status
        by[delta]['n'] += 1
        by[delta]['year'] += resolved and '✔' not in year
        by[delta]['title'] += resolved and '✔' not in title
        by[delta]['unresolved'] += not resolved
    print(f'\n===== {label}  n={len(cases)}, correct label {expect} =====')
    print(f'network attempts: {len(H.ATTEMPTS)}')
    print(f"{'delta':>6}{'n':>5}{'year flagged':>14}{'title flagged':>15}{'unresolved':>12}")
    tot = collections.Counter()
    for d in sorted(by):
        b = by[d]
        tot.update(b)
        print(f"{d:>6}{b['n']:>5}{b['year']:>14}{b['title']:>15}{b['unresolved']:>12}")
    print(f"  year flagged overall: {tot['year']}/{tot['n']} "
          f"= {100 * tot['year'] / tot['n']:.1f}%")


if __name__ == '__main__':
    real = H.cohort_real_pairs()
    report(real, 'real_preprint_pairs (as written)', 'LOW (must not flag)')
    report(H.mask_years_outside_printed_slot(real),
           'real_preprint_pairs (year isolated)', 'LOW (must not flag)')
    report(H.cohort_chimeras(), 'year_only_chimeras', 'HIGH (must flag)')
    print(f'\ntotal network attempts: {len(H.ATTEMPTS)}')
