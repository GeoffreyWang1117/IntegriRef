"""Run pvsundar/bibliography-verification-tool unmodified and offline.

What this measures: the tool does compare the year on its DOI route, with a
+-2 tolerance, and the mismatch label it produces never reaches its output.
See README.md for the mechanism and the line numbers.

  python benchmarks/independent_checkers/run_pvsundar.py
"""
import collections
import importlib.util
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H                                              # noqa: E402

H.install_guard()
ENTRY = H.find_clone('pvsundar', 'PVSUNDAR_CLONE')
spec = importlib.util.spec_from_file_location('pvs', ENTRY)
pvs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pvs)

# The two network entry points, rebound. Upstream decision logic is untouched.
BY_TITLE = {}


def check_crossref(title, author=None, year=None, doi=None):
    if doi:
        return (True, H.REG[doi.lower()]) if doi.lower() in H.REG else (False, None)
    if title and title in BY_TITLE:
        return True, BY_TITLE[title]
    return False, None


def check_pubmed(title, author=None):
    # Deliberately unanswered: PubMed is a second registry and would change
    # only the handful of rows whose DOI does not resolve. Recorded as a limit.
    return False, None


pvs.check_crossref, pvs.check_pubmed = check_crossref, check_pubmed

from docx import Document                                        # noqa: E402
OUT = Path(__file__).resolve().parent / '_scratch'
OUT.mkdir(exist_ok=True)


def run(cases, label):
    d = Document()
    d.add_paragraph('References')
    for _, _, line in cases:
        d.add_paragraph(line)
    p = OUT / f'bib_{label}.docx'
    d.save(p)
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        df, _ = pvs.verify_bibliography(str(p))
    finally:
        sys.stdout = old
    return df


def report(cases, label, expect):
    df = run(cases, label)
    by, scores = collections.defaultdict(collections.Counter), collections.defaultdict(list)
    issues, year_flagged = collections.Counter(), 0
    for (_, delta, _), (_, r) in zip(cases, df.iterrows()):
        by[delta][r['Status']] += 1
        scores[delta].append(r['CrossRef_Match_Score'])
        issues[str(r['Issues_Detected'])] += 1
        year_flagged += 'YEAR' in str(r['Issues_Detected'])
    print(f'\n===== {label}  n={len(cases)}, correct label {expect} =====')
    print(f'network attempts: {len(H.ATTEMPTS)}')
    print(f"{'delta':>6}{'n':>5}{'VERIFIED':>10}{'NEEDS_REVIEW':>14}{'median score':>14}")
    for d in sorted(by):
        s = sorted(scores[d])
        print(f"{d:>6}{sum(by[d].values()):>5}{by[d]['VERIFIED']:>10}"
              f"{by[d]['NEEDS_REVIEW']:>14}{s[len(s) // 2]:>14}")
    for k, v in issues.most_common():
        print(f'  Issues_Detected = {k!r}: {v}')
    print(f'references carrying a YEAR_MISMATCH label: {year_flagged}')
    return df


def positive_controls():
    """A zero that cannot fail is not evidence. These prove the issue channel
    works: a label appended after upstream's line 514 reaches the output, and
    the year label appended before it does not."""
    J, T = 'Journal of Applied Dynamics, 12(3), 45-67', \
           'Adaptive estimation of latent transport coefficients'

    def msg(doi, title, fam, yr):
        return {'DOI': doi, 'title': [title],
                'author': [{'family': fam, 'given': 'Hiroshi'}],
                'published-print': {'date-parts': [[yr, 1]]}}

    cases = []
    H.REG['10.1234/t13.a01'] = msg('10.1234/t13.a01', T, 'Nakamura', 2008)
    cases.append(('chimera_d7', 7, f'Nakamura, H. (2015). {T}. {J}. https://doi.org/10.1234/t13.a01'))
    BY_TITLE[T] = msg('10.1234/t13.a02', T, 'Nakamura', 2015)
    cases.append(('posctrl_no_doi', 0, f'Nakamura, H. (2015). {T}. {J}.'))
    cases.append(('posctrl_notfound', 0, f'Nakamura, H. (2015). {T}. {J}. https://doi.org/10.9999/absent'))
    H.REG['10.1234/t13.a04'] = msg('10.1234/t13.a04', 'Marine sediment core chronology revisited', 'Alvarez', 2015)
    cases.append(('posctrl_lowscore', 0, f'Nakamura, H. (2015). {T}. {J}. https://doi.org/10.1234/t13.a04'))
    H.REG['10.1234/t13.a05'] = msg('10.1234/t13.a05', T, 'Nakamura', 2008)
    cases.append(('inpress_d7', 7, f'Nakamura, H. (2099). {T}. {J}. https://doi.org/10.1234/t13.a05'))

    df = run(cases, 'positive_controls')
    print('\n===== positive controls =====')
    hdr = f"{'case':<19}{'printed':>8}{'registered':>11}{'score':>7}{'status':>14}  Issues_Detected"
    print(hdr)
    for (n, _, _), (_, r) in zip(cases, df.iterrows()):
        print(f"{n:<19}{str(r['Extracted_Year']):>8}{str(r['Verified_Year']):>11}"
              f"{r['CrossRef_Match_Score']:>7}{r['Status']:>14}  {r['Issues_Detected']}")


if __name__ == '__main__':
    positive_controls()
    report(H.cohort_real_pairs(), 'real_preprint_pairs', 'LOW (must not flag)')
    report(H.cohort_chimeras(), 'year_only_chimeras', 'HIGH (must flag)')
    print(f'\ntotal network attempts: {len(H.ATTEMPTS)}')
