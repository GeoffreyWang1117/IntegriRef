"""Base registry adapter — the contract all registry adapters implement.

Design principles:
  - No content storage — only verification results and metadata
  - Built-in rate limiting per registry
  - Circuit breaker per registry (auto-open on repeated failures)
  - Exponential backoff retry (3 attempts)
  - Unified error handling
  - Returns ICEntity objects
"""

from __future__ import annotations

import abc
import logging
import random
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .entity import ICEntity

logger = logging.getLogger(__name__)

# Lazy-initialized shared infrastructure
_circuit_breakers = None
_response_cache = None


def _get_circuit_breakers():
    global _circuit_breakers
    if _circuit_breakers is None:
        from .circuit_breaker import CircuitBreakerRegistry
        _circuit_breakers = CircuitBreakerRegistry(
            failure_threshold=5, recovery_timeout=30.0, window_size=60.0)
    return _circuit_breakers


def _get_response_cache():
    global _response_cache
    if _response_cache is None:
        from .cache import ResponseCache
        _response_cache = ResponseCache()
    return _response_cache


def configure_cache(redis_url: str = "", max_memory: int = 10_000):
    """Configure shared response cache (call once at startup)."""
    global _response_cache
    from .cache import ResponseCache
    _response_cache = ResponseCache(
        redis_url=redis_url, max_memory_entries=max_memory)


@dataclass
class RegistryInfo:
    """Metadata about a registry adapter."""
    name: str               # e.g. "crossref"
    domain: str             # e.g. "academic"
    base_url: str           # e.g. "https://api.crossref.org"
    auth_type: str          # "none" | "api_key_free" | "oauth"
    rate_limit: float       # seconds between requests
    coverage: str           # human-readable description
    entity_types: list[str] # what entity types this registry covers


class RegistryAdapter(abc.ABC):
    """Abstract base class for all registry adapters.

    Subclasses must implement:
      - info() → RegistryInfo
      - query_by_id(id_type, id_value) → Optional[ICEntity]
      - search(title, ...) → list[ICEntity]

    Built-in features (transparent to subclasses):
      - Per-adapter rate limiting
      - Circuit breaker (5 failures in 60s → open for 30s)
      - Exponential backoff retry (3 attempts)
      - Structured logging
    """

    _last_call: float = 0.0

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "IntegriRef/1.0 (cross-domain reference verification)"
        })

    @abc.abstractmethod
    def info(self) -> RegistryInfo:
        """Return metadata about this registry."""
        ...

    @abc.abstractmethod
    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        """Look up a specific entity by its identifier.

        Args:
            id_type: e.g. "doi", "patent_number", "case_cite"
            id_value: the actual identifier value

        Returns:
            ICEntity if found, None otherwise.
        """
        ...

    @abc.abstractmethod
    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        """Search for entities by metadata fields.

        Returns a list of candidate ICEntity objects (best matches first).
        """
        ...

    # ── Rate limiting ─────────────────────────────────────────────────────

    def _rate_limit(self):
        """Enforce per-adapter rate limiting."""
        now = time.time()
        delay = self.info().rate_limit
        wait = delay - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self.__class__._last_call = time.time()

    # ── HTTP helpers with retry + circuit breaker ──────────────────────────

    def _get(self, url: str, params: dict = None,
             timeout: int = 20, **kwargs) -> Optional[requests.Response]:
        """Rate-limited GET with circuit breaker and retry.

        - Circuit breaker: skips request if registry is in OPEN state
        - Retry: 3 attempts with exponential backoff (1s, 2s, 4s) + jitter
        - Rate limiting: respects per-adapter delay
        """
        name = self.info().name
        cb = _get_circuit_breakers().get(name)

        if not cb.allow_request():
            logger.debug("[%s] Circuit OPEN — skipping GET %s", name, url)
            return None

        self._rate_limit()

        last_exc = None
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params,
                                         timeout=timeout, **kwargs)
                resp.raise_for_status()
                cb.record_success()
                return resp
            except requests.exceptions.HTTPError as e:
                last_exc = e
                status = e.response.status_code if e.response is not None else 0
                if status == 429:
                    # Rate limited — longer backoff
                    wait = min(2 ** (attempt + 2) + random.uniform(0, 1), 30)
                    logger.debug("[%s] HTTP 429, backoff %.1fs", name, wait)
                    time.sleep(wait)
                    continue
                if 400 <= status < 500:
                    # Client error (not 429) — don't retry
                    cb.record_failure()
                    return None
                # 5xx — retry
                logger.debug("[%s] HTTP %d (attempt %d/3)",
                             name, status, attempt + 1)
            except requests.RequestException as e:
                last_exc = e
                logger.debug("[%s] Request error (attempt %d/3): %s",
                             name, attempt + 1, type(e).__name__)

            if attempt < 2:
                wait = min(2 ** attempt + random.uniform(0, 0.5), 8)
                time.sleep(wait)

        cb.record_failure()
        logger.warning("[%s] All 3 GET attempts failed: %s", name, last_exc)
        return None

    def _post(self, url: str, data: dict = None, json: dict = None,
              timeout: int = 30, **kwargs) -> Optional[requests.Response]:
        """Rate-limited POST with circuit breaker and retry."""
        name = self.info().name
        cb = _get_circuit_breakers().get(name)

        if not cb.allow_request():
            logger.debug("[%s] Circuit OPEN — skipping POST %s", name, url)
            return None

        self._rate_limit()

        last_exc = None
        for attempt in range(3):
            try:
                resp = self._session.post(url, data=data, json=json,
                                          timeout=timeout, **kwargs)
                resp.raise_for_status()
                cb.record_success()
                return resp
            except requests.exceptions.HTTPError as e:
                last_exc = e
                status = e.response.status_code if e.response is not None else 0
                if status == 429:
                    wait = min(2 ** (attempt + 2) + random.uniform(0, 1), 30)
                    time.sleep(wait)
                    continue
                if 400 <= status < 500:
                    cb.record_failure()
                    return None
            except requests.RequestException as e:
                last_exc = e

            if attempt < 2:
                wait = min(2 ** attempt + random.uniform(0, 0.5), 8)
                time.sleep(wait)

        cb.record_failure()
        logger.warning("[%s] All 3 POST attempts failed: %s", name, last_exc)
        return None

    # ── Convenience ───────────────────────────────────────────────────────

    def verify_exists(self, id_type: str, id_value: str) -> bool:
        """Quick existence check — does this ID resolve in this registry?"""
        return self.query_by_id(id_type, id_value) is not None

    def __repr__(self) -> str:
        i = self.info()
        return f"<{i.name} [{i.domain}] {i.base_url}>"
