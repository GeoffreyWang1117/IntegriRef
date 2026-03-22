"""Async registry adapter — aiohttp-based base class with circuit breaker, retry, cache.

Drop-in async counterpart of RegistryAdapter. Subclasses implement the same
interface but use async/await for non-blocking I/O.

Features over sync RegistryAdapter:
  - aiohttp with connection pooling (shared TCPConnector)
  - Per-registry circuit breaker (auto-open on repeated failures)
  - Exponential backoff retry (configurable)
  - Dual-layer caching (LRU + Redis)
  - Structured logging with timing
  - Async rate limiting (no thread blocking)
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp

from .cache import ResponseCache
from .circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState
from .entity import ICEntity
from .registry import RegistryInfo

logger = logging.getLogger(__name__)

# Module-level shared resources
_shared_session: Optional[aiohttp.ClientSession] = None
_shared_connector: Optional[aiohttp.TCPConnector] = None
_circuit_breakers = CircuitBreakerRegistry(
    failure_threshold=5, recovery_timeout=30.0, window_size=60.0)
_response_cache = ResponseCache()


async def get_shared_session() -> aiohttp.ClientSession:
    """Get or create a shared aiohttp session with connection pooling."""
    global _shared_session, _shared_connector
    if _shared_session is None or _shared_session.closed:
        _shared_connector = aiohttp.TCPConnector(
            limit=100,              # Total connection pool
            limit_per_host=10,      # Per-host limit
            ttl_dns_cache=300,      # DNS cache 5min
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(
            total=30,
            connect=5,
            sock_read=20,
        )
        _shared_session = aiohttp.ClientSession(
            connector=_shared_connector,
            timeout=timeout,
            headers={
                "User-Agent": "IntegriRef/1.0 (cross-domain reference verification)",
            },
        )
    return _shared_session


async def close_shared_session():
    """Cleanup shared session (call on app shutdown)."""
    global _shared_session, _shared_connector
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
    _shared_session = None
    _shared_connector = None


def configure_cache(redis_url: str = "", max_memory: int = 10_000):
    """Configure the shared response cache."""
    global _response_cache
    _response_cache = ResponseCache(
        redis_url=redis_url, max_memory_entries=max_memory)


def get_cache() -> ResponseCache:
    return _response_cache


def get_circuit_breakers() -> CircuitBreakerRegistry:
    return _circuit_breakers


class AsyncRegistryAdapter(abc.ABC):
    """Async base class for registry adapters.

    Subclasses implement:
      - info() → RegistryInfo
      - async query_by_id(id_type, id_value) → Optional[ICEntity]
      - async search(title, author, year) → list[ICEntity]
    """

    _last_call: float = 0.0

    def __init__(self):
        self._cb: Optional[CircuitBreaker] = None

    def _get_circuit_breaker(self) -> CircuitBreaker:
        if self._cb is None:
            self._cb = _circuit_breakers.get(self.info().name)
        return self._cb

    @abc.abstractmethod
    def info(self) -> RegistryInfo:
        ...

    @abc.abstractmethod
    async def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        ...

    @abc.abstractmethod
    async def search(self, title: str, author: str = "",
                     year: str = "", **kwargs) -> list[ICEntity]:
        ...

    # ── Async rate limiting ────────────────────────────────────────────

    async def _rate_limit(self):
        """Non-blocking rate limit (asyncio.sleep instead of time.sleep)."""
        now = time.time()
        delay = self.info().rate_limit
        wait = delay - (now - self.__class__._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self.__class__._last_call = time.time()

    # ── HTTP helpers with retry + circuit breaker + cache ─────────────

    async def _get(self, url: str, params: dict = None,
                   timeout: float = 20, headers: dict = None,
                   cache_key: str = "", cache_ttl: int = 0,
                   **kwargs) -> Optional[aiohttp.ClientResponse]:
        """Async GET with circuit breaker, retry, and optional caching.

        Args:
            url: Request URL
            params: Query parameters
            timeout: Request timeout in seconds
            headers: Additional headers
            cache_key: If set, cache the response body
            cache_ttl: Cache TTL in seconds
        """
        cb = self._get_circuit_breaker()
        if not cb.allow_request():
            logger.debug("[%s] Circuit OPEN, skipping request to %s",
                         self.info().name, url)
            return None

        # Check cache
        if cache_key:
            cached = _response_cache.get(cache_key)
            if cached is not None:
                return _CachedResponse(cached)

        await self._rate_limit()

        session = await get_shared_session()
        req_timeout = aiohttp.ClientTimeout(total=timeout)

        last_exc = None
        for attempt in range(3):
            try:
                resp = await session.get(
                    url, params=params, timeout=req_timeout,
                    headers=headers, **kwargs)
                resp.raise_for_status()
                cb.record_success()

                # Cache if requested
                if cache_key and cache_ttl > 0:
                    try:
                        body = await resp.text()
                        _response_cache.set(cache_key, body, ttl=cache_ttl)
                        return _CachedResponse(body)
                    except Exception:
                        pass
                return resp

            except asyncio.TimeoutError:
                last_exc = TimeoutError(f"Timeout after {timeout}s")
                logger.debug("[%s] Timeout (attempt %d/3): %s",
                             self.info().name, attempt + 1, url)
            except aiohttp.ClientResponseError as e:
                last_exc = e
                if e.status == 429:
                    # Rate limited — backoff longer
                    wait = min(2 ** (attempt + 2), 30)
                    logger.debug("[%s] Rate limited, backoff %ds",
                                 self.info().name, wait)
                    await asyncio.sleep(wait)
                    continue
                if e.status >= 500:
                    logger.debug("[%s] Server error %d (attempt %d/3)",
                                 self.info().name, e.status, attempt + 1)
                else:
                    # 4xx (not 429) — don't retry
                    cb.record_failure()
                    return None
            except aiohttp.ClientError as e:
                last_exc = e
                logger.debug("[%s] Client error (attempt %d/3): %s",
                             self.info().name, attempt + 1, e)
            except Exception as e:
                last_exc = e
                logger.debug("[%s] Unexpected error (attempt %d/3): %s",
                             self.info().name, attempt + 1, e)

            # Exponential backoff with jitter
            if attempt < 2:
                import random
                wait = min(2 ** attempt + random.uniform(0, 1), 10)
                await asyncio.sleep(wait)

        cb.record_failure()
        logger.warning("[%s] All 3 attempts failed: %s", self.info().name, last_exc)
        return None

    async def _post(self, url: str, data: dict = None, json_data: dict = None,
                    timeout: float = 30, headers: dict = None,
                    **kwargs) -> Optional[aiohttp.ClientResponse]:
        """Async POST with circuit breaker and retry."""
        cb = self._get_circuit_breaker()
        if not cb.allow_request():
            return None

        await self._rate_limit()
        session = await get_shared_session()
        req_timeout = aiohttp.ClientTimeout(total=timeout)

        last_exc = None
        for attempt in range(3):
            try:
                resp = await session.post(
                    url, data=data, json=json_data,
                    timeout=req_timeout, headers=headers, **kwargs)
                resp.raise_for_status()
                cb.record_success()
                return resp
            except asyncio.TimeoutError:
                last_exc = TimeoutError(f"POST timeout after {timeout}s")
            except aiohttp.ClientResponseError as e:
                last_exc = e
                if e.status == 429:
                    wait = min(2 ** (attempt + 2), 30)
                    await asyncio.sleep(wait)
                    continue
                if e.status < 500:
                    cb.record_failure()
                    return None
            except (aiohttp.ClientError, Exception) as e:
                last_exc = e

            if attempt < 2:
                import random
                wait = min(2 ** attempt + random.uniform(0, 1), 10)
                await asyncio.sleep(wait)

        cb.record_failure()
        return None

    # ── Convenience ──────────────────────────────────────────────────

    async def verify_exists(self, id_type: str, id_value: str) -> bool:
        return (await self.query_by_id(id_type, id_value)) is not None

    def __repr__(self) -> str:
        i = self.info()
        return f"<Async:{i.name} [{i.domain}] {i.base_url}>"


class _CachedResponse:
    """Lightweight wrapper to make cached text look like aiohttp response."""

    def __init__(self, body: str | dict):
        if isinstance(body, dict):
            import json as _json
            self._text = _json.dumps(body)
            self._json = body
        else:
            self._text = body
            self._json = None
        self.status = 200

    async def text(self) -> str:
        return self._text

    async def json(self, **kwargs) -> Any:
        if self._json is not None:
            return self._json
        import json as _json
        return _json.loads(self._text)

    async def read(self) -> bytes:
        return self._text.encode()

    def raise_for_status(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass
