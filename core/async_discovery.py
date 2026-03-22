"""Async registry discovery — asyncio-based parallel queries across registries.

Drop-in async replacement for RegistryDiscovery's parallel methods.
Uses asyncio.gather for true concurrent I/O instead of ThreadPoolExecutor.

Usage:
    discovery = AsyncRegistryDiscovery()
    discovery.register(CrossRefAsyncAdapter())
    discovery.register(OpenAlexAsyncAdapter())

    results = await discovery.parallel_query_by_id("doi", "10.1234/test")
    results = await discovery.parallel_search("Attention Is All You Need")
    report  = await discovery.verify_reference(title="...", identifier="10.xxx")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .async_registry import AsyncRegistryAdapter
from .cache import ResponseCache
from .circuit_breaker import CircuitBreakerRegistry, CircuitState
from .discovery import RegistryDiscovery  # Reuse detect_id_type and routing logic
from .entity import ICEntity
from .registry import RegistryAdapter

logger = logging.getLogger(__name__)


class AsyncRegistryDiscovery:
    """Async-native registry discovery with concurrent queries.

    Supports both sync RegistryAdapter (wrapped in executor) and native
    AsyncRegistryAdapter instances.
    """

    def __init__(self, cache: ResponseCache = None):
        self._async_registries: list[AsyncRegistryAdapter] = []
        self._sync_registries: list[RegistryAdapter] = []
        self._domain_map: dict[str, list] = {}
        self._cache = cache or ResponseCache()
        self._circuit_breakers = CircuitBreakerRegistry()

    def register(self, adapter: AsyncRegistryAdapter | RegistryAdapter):
        """Register an adapter (sync or async)."""
        info = adapter.info()
        if isinstance(adapter, AsyncRegistryAdapter):
            self._async_registries.append(adapter)
        else:
            self._sync_registries.append(adapter)
        self._domain_map.setdefault(info.domain, []).append(adapter)

    def register_all(self, adapters: list):
        for adapter in adapters:
            if isinstance(adapter, type):
                adapter = adapter()
            self.register(adapter)

    @property
    def all_adapters(self) -> list:
        return self._async_registries + self._sync_registries

    def list_registries(self) -> list[dict]:
        return [
            {
                "name": a.info().name,
                "domain": a.info().domain,
                "coverage": a.info().coverage,
                "async": isinstance(a, AsyncRegistryAdapter),
                "circuit_state": self._circuit_breakers.get(
                    a.info().name).state.value,
            }
            for a in self.all_adapters
        ]

    # ── Async query methods ────────────────────────────────────────

    async def parallel_query_by_id(
        self, id_type: str, id_value: str,
        domains: list[str] = None,
        timeout: float = 15.0,
    ) -> list[tuple[str, ICEntity]]:
        """Query all registries for an ID in parallel.

        Returns list of (registry_name, entity) tuples.
        """
        adapters = self._filter_adapters(domains)
        if not adapters:
            return []

        tasks = []
        for adapter in adapters:
            name = adapter.info().name

            # Skip if circuit is open
            cb = self._circuit_breakers.get(name)
            if cb.state == CircuitState.OPEN:
                continue

            # Check cache first
            cache_key = ResponseCache.make_key(name, id_type, id_value)
            found, cached = self._cache.get_or_none(cache_key)
            if found:
                if cached is not None:
                    # Reconstruct ICEntity from cache
                    tasks.append(self._return_cached(name, cached))
                continue

            tasks.append(self._query_one(adapter, id_type, id_value, cache_key))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, tuple) and r[1] is not None]

    async def parallel_search(
        self, title: str, author: str = "", year: str = "",
        domains: list[str] = None,
        timeout: float = 15.0,
    ) -> list[tuple[str, list[ICEntity]]]:
        """Search all registries in parallel.

        Returns list of (registry_name, hits) tuples.
        """
        adapters = self._filter_adapters(domains)
        if not adapters:
            return []

        tasks = []
        for adapter in adapters:
            name = adapter.info().name
            cb = self._circuit_breakers.get(name)
            if cb.state == CircuitState.OPEN:
                continue

            cache_key = ResponseCache.make_key(
                name, "search", f"{title}|{author}|{year}")
            found, cached = self._cache.get_or_none(cache_key)
            if found:
                if cached is not None and isinstance(cached, list):
                    tasks.append(self._return_cached_list(name, cached))
                continue

            tasks.append(self._search_one(adapter, title, author, year, cache_key))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results
                if isinstance(r, tuple) and r[1] is not None and len(r[1]) > 0]

    async def verify_reference(
        self, title: str = "", identifier: str = "",
        author: str = "", year: str = "",
        domains: list[str] = None,
    ) -> dict:
        """High-level async verification: ID lookup → search fallback.

        Returns same structure as sync RegistryDiscovery.verify_reference().
        """
        registries_tried = set()
        registries_hit = set()
        matches = []

        # Step 1: ID-based lookup
        if identifier:
            id_type = RegistryDiscovery.detect_id_type(identifier)
            if id_type:
                results = await self.parallel_query_by_id(
                    id_type, identifier, domains=domains)
                for name, entity in results:
                    registries_tried.add(name)
                    registries_hit.add(name)
                    matches.append(entity)

        # Step 2: Search-based lookup (only if ID didn't find anything)
        if not matches and title:
            results = await self.parallel_search(
                title, author=author, year=year, domains=domains)
            for name, hits in results:
                registries_tried.add(name)
                if hits:
                    registries_hit.add(name)
                    matches.extend(hits)

        return {
            "found": len(matches) > 0,
            "matches": matches,
            "registries_tried": sorted(registries_tried),
            "registries_hit": sorted(registries_hit),
        }

    # ── Internal helpers ───────────────────────────────────────────

    async def _query_one(self, adapter, id_type: str, id_value: str,
                         cache_key: str) -> tuple[str, Optional[ICEntity]]:
        """Query a single adapter (async or sync-wrapped)."""
        name = adapter.info().name
        start = time.monotonic()
        try:
            if isinstance(adapter, AsyncRegistryAdapter):
                entity = await asyncio.wait_for(
                    adapter.query_by_id(id_type, id_value), timeout=15.0)
            else:
                loop = asyncio.get_event_loop()
                entity = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, adapter.query_by_id, id_type, id_value),
                    timeout=15.0)

            elapsed = time.monotonic() - start
            logger.debug("[%s] query_by_id(%s, %s) → %s (%.1fms)",
                         name, id_type, id_value,
                         "found" if entity else "not found",
                         elapsed * 1000)

            # Cache result
            if entity:
                self._cache.set(cache_key, self._entity_to_dict(entity),
                                ttl=ResponseCache.TTL_ID_LOOKUP)
            else:
                self._cache.set_not_found(cache_key)

            return (name, entity)

        except asyncio.TimeoutError:
            logger.debug("[%s] query_by_id timed out", name)
            return (name, None)
        except Exception as e:
            logger.debug("[%s] query_by_id error: %s", name, e)
            return (name, None)

    async def _search_one(self, adapter, title: str, author: str,
                          year: str, cache_key: str
                          ) -> tuple[str, list[ICEntity]]:
        """Search a single adapter."""
        name = adapter.info().name
        try:
            if isinstance(adapter, AsyncRegistryAdapter):
                hits = await asyncio.wait_for(
                    adapter.search(title, author=author, year=year),
                    timeout=15.0)
            else:
                loop = asyncio.get_event_loop()
                hits = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, adapter.search, title, author, year),
                    timeout=15.0)

            if hits:
                self._cache.set(
                    cache_key,
                    [self._entity_to_dict(e) for e in hits],
                    ttl=ResponseCache.TTL_SEARCH)
            return (name, hits or [])

        except asyncio.TimeoutError:
            return (name, [])
        except Exception as e:
            logger.debug("[%s] search error: %s", name, e)
            return (name, [])

    async def _return_cached(self, name: str,
                             data: dict) -> tuple[str, Optional[ICEntity]]:
        """Return a cached entity."""
        entity = self._dict_to_entity(data)
        return (name, entity)

    async def _return_cached_list(self, name: str,
                                  data: list) -> tuple[str, list[ICEntity]]:
        """Return cached search results."""
        entities = [self._dict_to_entity(d) for d in data if d]
        return (name, entities)

    def _filter_adapters(self, domains: list[str] = None) -> list:
        adapters = self.all_adapters
        if domains:
            adapters = [a for a in adapters if a.info().domain in domains]
        return adapters

    @staticmethod
    def _entity_to_dict(entity: ICEntity) -> dict:
        """Serialize ICEntity for caching."""
        return {
            "title": entity.title,
            "authors": entity.authors,
            "year": entity.year,
            "venue": entity.venue,
            "entity_type": entity.entity_type.value if hasattr(entity.entity_type, 'value') else str(entity.entity_type),
            "external_ids": [
                {"registry": eid.registry, "id_type": eid.id_type,
                 "id_value": eid.id_value}
                for eid in (entity.external_ids or [])
            ] if hasattr(entity, 'external_ids') and entity.external_ids else [],
            "is_retracted": getattr(entity, 'is_retracted', False),
            "metadata": getattr(entity, 'metadata', {}),
            "source_registries": getattr(entity, 'source_registries', []),
        }

    @staticmethod
    def _dict_to_entity(data: dict) -> Optional[ICEntity]:
        """Deserialize ICEntity from cache."""
        if not data or not data.get("title"):
            return None
        try:
            from .entity import EntityType
            entity = ICEntity(
                entity_type=EntityType(data.get("entity_type", "unknown")),
                title=data["title"],
                authors=data.get("authors", []),
                year=data.get("year", ""),
                venue=data.get("venue", ""),
                metadata=data.get("metadata", {}),
                source_registries=data.get("source_registries", []),
            )
            if data.get("is_retracted"):
                entity.is_retracted = True
            for eid in data.get("external_ids", []):
                entity.add_external_id(
                    eid["registry"], eid["id_type"], eid["id_value"])
            return entity
        except Exception:
            return None
