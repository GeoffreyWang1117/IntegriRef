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


def _shared_update(provider: str, fn) -> bool:
    """Machine-wide turn-taking shared with bibguard, scout, arxiv-fetch and
    keys-doctor (2026-10-07): $RESEARCH_RATELIMIT_DIR or ~/.cache/research-ratelimit,
    <provider>.lock flock()ed while <provider>.json {"next_ok", "cooldown_until",
    "why"} is read and rewritten. False when unavailable (not POSIX, unwritable)."""
    import json
    import os
    from pathlib import Path
    try:
        import fcntl
        base = os.environ.get("RESEARCH_RATELIMIT_DIR")
        d = Path(base).expanduser() if base else \
            Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "research-ratelimit"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{provider}.lock", "a+") as lk:
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            sp = d / f"{provider}.json"
            try:
                state = json.loads(sp.read_text())
            except (OSError, ValueError):
                state = {}
            fn(state)
            sp.write_text(json.dumps(state))
        return True
    except (ImportError, OSError):
        return False


def polite_wait() -> None:
    """Serialize arXiv requests at >= MIN_INTERVAL (API lookups and e-prints), across
    every process on this machine; refuse instead of sleeping through a long shared
    cooldown another process recorded after a 429."""
    global _last_request
    box = {}

    def take(st):
        now = time.time()
        cool = float(st.get("cooldown_until", 0)) - now
        if cool > _MAX_RETRY_WAIT:
            box["refuse"] = (cool, st.get("why", "HTTP 429"))
            return
        until = max(float(st.get("next_ok", 0)), float(st.get("cooldown_until", 0)))
        if until > now:
            time.sleep(until - now)
        st["next_ok"] = time.time() + MIN_INTERVAL
    if _shared_update("arxiv", take):
        if "refuse" in box:
            cool, why = box["refuse"]
            raise SourceRefused("arxiv", f"cooling down {cool:.0f}s after {why} (shared across processes)")
        _last_request = time.monotonic()
        return
    gap = time.monotonic() - _last_request
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_request = time.monotonic()


def _shared_cooldown(provider: str, seconds: float, why: str) -> None:
    def cool(st):
        st["cooldown_until"] = max(float(st.get("cooldown_until", 0)), time.time() + seconds)
        st["why"] = why
    _shared_update(provider, cool)


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
            if e.code == 429 and source in ("arxiv", "datacite", "openreview"):
                _shared_cooldown(source, ra if ra is not None else 2.0, f"HTTP 429 to deepcite")
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
