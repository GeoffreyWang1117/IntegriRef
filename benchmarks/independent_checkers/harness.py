"""Shared harness for running third-party reference checkers offline.

Neither checker is vendored here: both are third-party code under their own
terms, and the point of this harness is that they run unmodified. Only the
network entry points are rebound, exactly as a caller injecting a transport
would, and a socket guard is installed -- and self-tested against a live URL --
so "no requests were made" is measured rather than asserted.
"""
import collections
import json
import re
import socket
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'benchmarks' / 'data'
CACHE = ROOT / 'benchmarks' / 'crossref_dates' / 'cache'

# --- clone discovery --------------------------------------------------------
# Upstream revisions this harness was written against:
PINNED = {
    'pvsundar': ('https://github.com/pvsundar/bibliography-verification-tool',
                 '2e060a56ab83824e6fbfa7c82a97df466d111649',
                 'verify_bibliography_production.py'),
    'binara': ('https://github.com/Binara-Sachin/doi-reference-validator',
               'b791684815723b334c8143b79053a79c430cbf88',
               'main.py'),
}


def find_clone(key, env_var):
    url, rev, entry = PINNED[key]
    candidates = [
        __import__('os').environ.get(env_var),
        ROOT / 'vendor' / url.rsplit('/', 1)[-1],
        ROOT / '.codex' / url.rsplit('/', 1)[-1],
    ]
    for c in candidates:
        if c and (Path(c) / entry).exists():
            return Path(c) / entry
    print(f'No clone of {url} found. Tried:', file=sys.stderr)
    for c in candidates:
        print(f'  {c}', file=sys.stderr)
    print(f'\n  git clone {url} vendor/{url.rsplit("/", 1)[-1]}\n'
          f'  git -C vendor/{url.rsplit("/", 1)[-1]} checkout {rev}\n', file=sys.stderr)
    sys.exit(2)


# --- socket guard -----------------------------------------------------------
_real_gai, _real_conn = socket.getaddrinfo, socket.socket.connect
_LOCAL = {'127.0.0.1', 'localhost', '::1', '0.0.0.0', ''}
ATTEMPTS = []


class NetworkBlocked(OSError):
    pass


def _gai(host, port, *a, **k):
    if host in _LOCAL or host is None:
        return _real_gai(host, port, *a, **k)
    ATTEMPTS.append(('resolve', host))
    raise NetworkBlocked(f'NETWORK ATTEMPT: resolving {host!r}')


def _conn(self, addr):
    h = addr[0] if isinstance(addr, tuple) else addr
    if h in _LOCAL or h is None:
        return _real_conn(self, addr)
    ATTEMPTS.append(('connect', h))
    raise NetworkBlocked(f'NETWORK ATTEMPT: connecting {h!r}')


def install_guard():
    """Block non-local sockets, then prove the block actually fires."""
    socket.getaddrinfo, socket.socket.connect = _gai, _conn
    import requests
    try:
        requests.get('https://api.crossref.org/works/10.1000/guard-self-test', timeout=5)
    except Exception as exc:
        if 'NETWORK ATTEMPT' not in repr(exc):
            print(f'GUARD SELF-TEST: inconclusive -- {exc!r}', file=sys.stderr)
            sys.exit(3)
        print(f'guard self-test: ok, blocked and recorded '
              f'({ATTEMPTS[-1][0]} {ATTEMPTS[-1][1]})')
        ATTEMPTS.clear()
        return
    print('GUARD SELF-TEST: FAILED -- the request went through', file=sys.stderr)
    sys.exit(3)


# --- cohorts ----------------------------------------------------------------
REG = {}        # doi (lowercased) -> Crossref `message` dict


def _apa_name(a):
    a = a.strip()
    fam, giv = (a.split(',', 1) if ',' in a else (a.split()[-1], ' '.join(a.split()[:-1])))
    fam, giv = fam.strip(), giv.strip()
    return f'{fam}, {giv[0]}.' if giv else f'{fam}, X.'


def _line(first_author, year, title, venue, doi):
    return f'{_apa_name(first_author)} ({year}). {title}. {venue}. https://doi.org/{doi}'


def cohort_real_pairs():
    """483 real preprint->publication pairs, each against its real, frozen
    Crossref response. Correct label LOW: a checker must not flag these."""
    src = sorted(p for p in DATA.glob('preprint_hard_negatives_2*.jsonl')
                 if 'crossref' not in p.name)[-1]
    out = []
    for r in (json.loads(l) for l in src.read_text(encoding='utf-8').splitlines() if l):
        doi = r['registered']['doi']
        p = CACHE / (urllib.parse.quote(doi, safe='') + '.json')
        blob = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'_fetch_error': {}}
        if '_fetch_error' not in blob:
            REG[doi.lower()] = blob['message']
        out.append((r['case_id'], r['delta'],
                    _line(r['printed']['authors'].split(',')[0], r['printed']['year'],
                          r['printed']['title'], r['printed']['venue'], doi)))
    return out


def cohort_chimeras():
    """60 year-only chimeras. The registered record is a deterministic proxy
    built from the paired real control row, not a live Crossref response.
    Correct label HIGH: a checker must flag these."""
    rows = [json.loads(l) for l in
            (DATA / 'idchim_year_only.jsonl').read_text(encoding='utf-8').splitlines() if l]
    bydoi = collections.defaultdict(dict)
    for r in rows:
        bydoi[r['doi']][r['category']] = r
    out = []
    for doi, pair in bydoi.items():
        ch, ct = pair['chimera'], pair['real_control']
        auth = json.loads(ct['authors'])
        REG[doi.lower()] = {
            'DOI': doi, 'title': [ct['title']],
            'author': [{'family': a.split(',')[0].strip(),
                        'given': (a.split(',')[1].strip() if ',' in a else '')} for a in auth],
            'published-print': {'date-parts': [[int(ct['year']), 1]]}}
        out.append((ch['id'], abs(int(ct['year']) - int(ch['year'])),
                    _line(auth[0], ch['year'], ch['title'], ct['venue'], doi)))
    return out


def mask_years_outside_printed_slot(cases):
    """Every one of the 483 journal-ref venue strings contains the registered
    year, so a substring year test can pass on text that has nothing to do with
    the printed year. This variant masks those digits to isolate the year
    comparison; it is a control, not the realistic input."""
    out = []
    for name, delta, line in cases:
        head, sep, tail = line.rpartition('. https://doi.org/')
        m = re.match(r'^(.*?\(\d{4}\)\.)(.*)$', head, re.DOTALL)
        out.append((name, delta,
                    (m.group(1) + re.sub(r'\d{4}', 'NNNN', m.group(2)) + sep + tail)
                    if m else line))
    return out
