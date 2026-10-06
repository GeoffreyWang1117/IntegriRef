"""HTTP for deepcite: one place that tells "the source answered" from "it did not".

A source that does not answer (HTTP 429/5xx, timeout, reset) raises SourceRefused.
Everything else -- including 404, which IS an answer -- is returned. Until
2026-10-06 every failure came back as None, so arXiv rate-limiting this machine
was reported as "no arXiv artifact found; supply one with --artifact".
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

USER_AGENT = "integriref-deepcite/0.2 (+https://github.com/GeoffreyWang1117/IntegriRef)"
MIN_INTERVAL = 3.0          # arXiv asks for roughly one request per three seconds
_RETRY_STATUSES = (429, 502, 503, 504)
_MAX_RETRY_WAIT = 8.0
_last_request = 0.0


class SourceRefused(RuntimeError):
    """A lookup or fetch source did not answer (HTTP 429/5xx, timeout, network)."""

    def __init__(self, source: str, reason: str):
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


def polite_wait() -> None:
    """Serialize arXiv requests at >= MIN_INTERVAL (API lookups and e-prints)."""
    global _last_request
    gap = time.monotonic() - _last_request
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_request = time.monotonic()


def http_get(url: str, source: str, timeout: int = 60, wait=None,
             max_bytes: int | None = None) -> bytes | None:
    """GET returning the body, None on 404/410 (an answer: not there), raising
    SourceRefused when the source does not answer. One retry when the source
    asks for a short wait (Retry-After <= 8 s, or none given on a 429/5xx)."""
    for attempt in range(2):
        if wait:
            wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                body = fh.read((max_bytes + 1) if max_bytes else -1)
            if max_bytes and len(body) > max_bytes:
                raise SourceRefused(source, f"artifact larger than {max_bytes >> 20} MB")
            return body
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                ra = float(ra) if ra is not None else None
            except ValueError:
                ra = None
            if e.code in _RETRY_STATUSES and attempt == 0 and (ra is None or ra <= _MAX_RETRY_WAIT):
                time.sleep(ra if ra is not None else 2.0)
                continue
            why = f"HTTP {e.code}"
            if e.code == 429 and ra and ra > _MAX_RETRY_WAIT:
                why += f" (retry after {ra / 60:.0f} min)"
            raise SourceRefused(source, why)
        except SourceRefused:
            raise
        except Exception as e:      # timeout, DNS, connection reset
            raise SourceRefused(source, f"no answer ({type(e).__name__})")
    raise SourceRefused(source, "no answer")
