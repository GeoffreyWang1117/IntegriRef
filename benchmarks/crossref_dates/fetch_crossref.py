"""Serial, cached Crossref fetcher for the T10 DOI fixture."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'data'
# newest build wins, so a rebuilt fixture is picked up without editing this file
FIXTURE = sorted(p for p in DATA.glob('preprint_hard_negatives_2*.jsonl') if 'crossref' not in p.name)[-1]
CACHE = ROOT / 'cache'
UA = 'IntegriRef-research/1.0 (+https://github.com/GeoffreyWang1117/IntegriRef)'
MIN_INTERVAL = 1.05
MAX_RETRIES = 5

rows = [json.loads(line) for line in FIXTURE.read_text(encoding='utf-8').splitlines() if line.strip()]
dois = sorted({row['registered']['doi'] for row in rows})
CACHE.mkdir(parents=True, exist_ok=True)
last_start = None
counts = {'total_dois': len(dois), 'success': 0, 'failed': 0, 'cache_hit': 0, 'network_requests': 0, 'retries': 0}
failures = []

for i, doi in enumerate(dois, 1):
    cache_file = CACHE / (urllib.parse.quote(doi, safe='') + '.json')
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding='utf-8'))
            counts['cache_hit'] += 1
            counts['failed' if '_fetch_error' in cached else 'success'] += 1
            if '_fetch_error' in cached:
                if cached.get('_fetch_error', {}).get('status') == 404 and cached.get('_attempts') != 1:
                    # The 404 was not retried; correct the initial writer's global-counter mistake.
                    cached['_attempts'] = 1
                    cache_file.write_text(json.dumps(cached, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
                failures.append({'doi': doi, 'error': cached['_fetch_error']})
            continue
        except Exception as e:
            # Preserve the bad cache for audit and report it; never silently refetch/overwrite.
            counts['failed'] += 1
            failures.append({'doi': doi, 'error': f'cache parse failed: {e}'})
            continue

    url = 'https://api.crossref.org/works/' + urllib.parse.quote(doi, safe='')
    result = None
    last_error = None
    attempts_for_doi = 0
    for attempt in range(MAX_RETRIES + 1):
        if last_start is not None:
            delay = MIN_INTERVAL - (time.monotonic() - last_start)
            if delay > 0:
                time.sleep(delay)
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        last_start = time.monotonic()
        counts['network_requests'] += 1
        attempts_for_doi += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                status = response.status
                if status == 200:
                    result = json.loads(raw.decode('utf-8'))
                    break
                last_error = {'status': status, 'body': raw.decode('utf-8', 'replace')[:2000]}
                if status != 429 and status < 500:
                    break
        except urllib.error.HTTPError as e:
            raw = e.read()
            last_error = {'status': e.code, 'body': raw.decode('utf-8', 'replace')[:2000]}
            if e.code != 429 and e.code < 500:
                break
        except Exception as e:
            last_error = {'error': f'{type(e).__name__}: {e}'}
            # Transport failures are retried with the same backoff to avoid silent loss.
        if attempt < MAX_RETRIES:
            counts['retries'] += 1
            backoff = min(2 ** attempt, 30)
            print(json.dumps({'event': 'retry', 'doi': doi, 'attempt': attempt + 1, 'backoff_seconds': backoff, 'last_error': last_error}), flush=True)
            time.sleep(backoff)

    if result is None:
        envelope = {'_fetch_error': last_error or {'error': 'no response'}, '_attempts': attempts_for_doi, 'doi': doi, 'url': url, 'user_agent': UA}
        cache_file.write_text(json.dumps(envelope, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        counts['failed'] += 1
        failures.append({'doi': doi, 'error': last_error})
    else:
        cache_file.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        counts['success'] += 1
    if i % 20 == 0 or i == len(dois):
        print(json.dumps({'event': 'progress', 'completed': i, **counts}), flush=True)

print(json.dumps({'event': 'final', **counts, 'failures': failures}, ensure_ascii=False), flush=True)
