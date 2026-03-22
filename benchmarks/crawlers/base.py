"""Base crawler with rate limiting, checkpoint/resume, and deduplication.

All IntegriRef dataset crawlers inherit from BaseCrawler.
"""

from __future__ import annotations

import abc
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

CONTACT_EMAIL = os.getenv("CROSSREF_EMAIL", "integriref-bench@example.com")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")

# Per-host rate limits (seconds between requests)
_RATE_LIMITS: dict[str, float] = {
    "api.crossref.org": 1.0,
    "doi.org": 1.0,
    "eutils.ncbi.nlm.nih.gov": 0.34 if not NCBI_API_KEY else 0.1,
    "www.ebi.ac.uk": 0.2,        # Europe PMC: 5 req/s
    "api.openalex.org": 0.1,     # OpenAlex: 10 req/s
}


class BaseCrawler(abc.ABC):
    """Base class for all IntegriRef dataset crawlers."""

    name: str = "base"

    def __init__(self, output_dir: Path, checkpoint: bool = True):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_enabled = checkpoint
        self._seen_ids: set[str] = set()
        self._last_request_time: dict[str, float] = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": f"IntegriRef-Benchmark/1.0 (mailto:{CONTACT_EMAIL})",
        })
        # Load seen IDs from disk
        self._seen_path = self.output_dir / f".seen_ids_{self.name}.txt"
        if self._seen_path.exists():
            with open(self._seen_path) as f:
                self._seen_ids = {line.strip() for line in f if line.strip()}
            logger.info("Loaded %d seen IDs for %s", len(self._seen_ids), self.name)

    @abc.abstractmethod
    def crawl(self, count: int, **kwargs) -> int:
        """Run the crawler. Returns number of records written."""
        ...

    # ── HTTP with rate limiting + retry ──────────────────────────────────

    def _get(self, url: str, params: dict | None = None,
             timeout: int = 30, max_retries: int = 3) -> Optional[requests.Response]:
        """Rate-limited GET with exponential backoff retry.

        Returns None on permanent failures (404, 410) without retrying.
        Retries on transient errors (429, 5xx, network errors).
        """
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        rate = _RATE_LIMITS.get(host, 0.5)

        # Respect rate limit
        last = self._last_request_time.get(host, 0)
        elapsed = time.time() - last
        if elapsed < rate:
            time.sleep(rate - elapsed)

        for attempt in range(max_retries):
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
                self._last_request_time[host] = time.time()

                # Don't retry on client errors (except 429)
                if resp.status_code in (404, 410, 400, 403):
                    return None

                if resp.status_code == 429:
                    wait = min(2 ** (attempt + 2), 60)
                    logger.warning("Rate limited by %s, waiting %ds", host, wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp

            except requests.exceptions.RequestException as e:
                wait = 2 ** (attempt + 1)
                if attempt < max_retries - 1:
                    logger.debug("Request failed (attempt %d/%d): %s. Retrying in %ds",
                                 attempt + 1, max_retries, e, wait)
                    time.sleep(wait)
                else:
                    logger.debug("All attempts failed for %s: %s", url, e)

        return None

    # ── JSONL I/O ────────────────────────────────────────────────────────

    def _append_jsonl(self, records: list[dict], path: Path) -> None:
        """Append records to a JSONL file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _write_jsonl(self, records: list[dict], path: Path) -> None:
        """Write records to a JSONL file (overwrite)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("Wrote %d records to %s", len(records), path)

    # ── Checkpoint ───────────────────────────────────────────────────────

    def _checkpoint_path(self) -> Path:
        return self.output_dir / f".checkpoint_{self.name}.json"

    def _save_checkpoint(self, offset: int, count: int, **extra: Any) -> None:
        if not self._checkpoint_enabled:
            return
        data = {"last_offset": offset, "count_written": count,
                "timestamp": time.time(), **extra}
        with open(self._checkpoint_path(), "w") as f:
            json.dump(data, f)

    def _load_checkpoint(self) -> dict:
        cp = self._checkpoint_path()
        if cp.exists():
            with open(cp) as f:
                data = json.load(f)
            logger.info("Resuming %s from checkpoint: offset=%d, count=%d",
                        self.name, data.get("last_offset", 0),
                        data.get("count_written", 0))
            return data
        return {}

    def _clear_checkpoint(self) -> None:
        cp = self._checkpoint_path()
        if cp.exists():
            cp.unlink()

    # ── Deduplication ────────────────────────────────────────────────────

    def _is_seen(self, id_value: str) -> bool:
        return id_value.lower().strip() in self._seen_ids

    def _mark_seen(self, id_value: str) -> None:
        normalized = id_value.lower().strip()
        self._seen_ids.add(normalized)

    def _flush_seen_ids(self) -> None:
        """Persist seen IDs to disk."""
        with open(self._seen_path, "w") as f:
            for sid in sorted(self._seen_ids):
                f.write(sid + "\n")
