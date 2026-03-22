"""Dual-layer cache — in-memory LRU + optional Redis for registry responses.

Layer 1: In-memory LRU (fast, per-process, no dependencies)
Layer 2: Redis (shared across processes, optional)

Usage:
    cache = ResponseCache(redis_url="redis://localhost:6379")
    # or without Redis:
    cache = ResponseCache()

    await cache.get("crossref:doi:10.1234/test")
    await cache.set("crossref:doi:10.1234/test", entity_dict, ttl=86400)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry with TTL."""
    value: Any
    expires_at: float
    created_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class LRUCache:
    """Thread-safe in-memory LRU cache with TTL support."""

    def __init__(self, max_size: int = 10_000):
        self._max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_expired:
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: int = 3600):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = CacheEntry(
                value=value,
                expires_at=time.time() + ttl,
            )

    def delete(self, key: str):
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
        }


class ResponseCache:
    """Dual-layer cache: in-memory LRU + optional Redis.

    TTL defaults:
      - DOI lookups: 24 hours (86400s)
      - Search results: 1 hour (3600s)
      - Not-found results: 10 minutes (600s) — negative caching
    """

    TTL_ID_LOOKUP = 86400      # 24h for ID-based lookups
    TTL_SEARCH = 3600          # 1h for search results
    TTL_NOT_FOUND = 600        # 10min for negative results
    TTL_RETRACTION = 43200     # 12h for retraction status

    def __init__(self, redis_url: str = "", max_memory_entries: int = 10_000):
        self._lru = LRUCache(max_size=max_memory_entries)
        self._redis = None
        self._redis_url = redis_url
        if redis_url:
            self._init_redis(redis_url)

    def _init_redis(self, url: str):
        try:
            import redis
            self._redis = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            self._redis.ping()
            logger.info("Redis cache connected: %s", url)
        except Exception as e:
            logger.warning("Redis unavailable, using memory-only cache: %s", e)
            self._redis = None

    @staticmethod
    def make_key(registry: str, query_type: str, query_value: str) -> str:
        """Build a deterministic cache key.

        Examples:
            make_key("crossref", "doi", "10.1234/test")
            make_key("openalex", "search", "Attention Is All You Need|Vaswani|2017")
        """
        raw = f"{registry}:{query_type}:{query_value}"
        short_hash = hashlib.md5(raw.encode()).hexdigest()[:12]
        return f"ir:{registry}:{query_type}:{short_hash}"

    def get(self, key: str) -> Optional[Any]:
        """Get from L1 (memory), then L2 (Redis)."""
        # L1: memory
        result = self._lru.get(key)
        if result is not None:
            return result

        # L2: Redis
        if self._redis:
            try:
                raw = self._redis.get(key)
                if raw is not None:
                    value = json.loads(raw)
                    # Promote to L1
                    self._lru.set(key, value, ttl=3600)
                    return value
            except Exception:
                pass

        return None

    def set(self, key: str, value: Any, ttl: int = 3600):
        """Set in both L1 and L2."""
        self._lru.set(key, value, ttl=ttl)

        if self._redis:
            try:
                self._redis.setex(key, ttl, json.dumps(value, default=str))
            except Exception:
                pass

    def get_or_none(self, key: str) -> tuple[bool, Optional[Any]]:
        """Distinguish 'not cached' from 'cached as None'.

        Returns (found_in_cache, value).
        """
        result = self.get(key)
        if result is not None:
            return True, result
        # Check if we have a negative cache entry
        neg_key = f"{key}:neg"
        neg = self._lru.get(neg_key)
        if neg is not None:
            return True, None
        if self._redis:
            try:
                if self._redis.exists(neg_key):
                    return True, None
            except Exception:
                pass
        return False, None

    def set_not_found(self, key: str):
        """Cache a negative result (not found in registry)."""
        neg_key = f"{key}:neg"
        self._lru.set(neg_key, True, ttl=self.TTL_NOT_FOUND)
        if self._redis:
            try:
                self._redis.setex(neg_key, self.TTL_NOT_FOUND, "1")
            except Exception:
                pass

    def invalidate(self, key: str):
        self._lru.delete(key)
        self._lru.delete(f"{key}:neg")
        if self._redis:
            try:
                self._redis.delete(key, f"{key}:neg")
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        s = self._lru.stats
        s["redis_connected"] = self._redis is not None
        return s
