"""Tests for the acceleration layer — cache, circuit breaker, async, batch, parsing."""

from __future__ import annotations

import asyncio
import time

import pytest


# ═══════════════════════════════════════════════════════════════════════
# 1. LRU Cache tests
# ═══════════════════════════════════════════════════════════════════════

class TestLRUCache:
    def test_basic_get_set(self):
        from core.cache import LRUCache
        cache = LRUCache(max_size=100)
        cache.set("key1", {"title": "Test"}, ttl=60)
        assert cache.get("key1") == {"title": "Test"}

    def test_miss_returns_none(self):
        from core.cache import LRUCache
        cache = LRUCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self):
        from core.cache import LRUCache
        cache = LRUCache()
        cache.set("key1", "value", ttl=0)  # Expire immediately
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_lru_eviction(self):
        from core.cache import LRUCache
        cache = LRUCache(max_size=3)
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.set("c", 3, ttl=60)
        cache.set("d", 4, ttl=60)  # Should evict "a"
        assert cache.get("a") is None
        assert cache.get("d") == 4

    def test_lru_access_refreshes_order(self):
        from core.cache import LRUCache
        cache = LRUCache(max_size=3)
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.set("c", 3, ttl=60)
        cache.get("a")  # Touch "a" → now most recent
        cache.set("d", 4, ttl=60)  # Should evict "b" (oldest untouched)
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_stats(self):
        from core.cache import LRUCache
        cache = LRUCache()
        cache.set("k", "v", ttl=60)
        cache.get("k")       # hit
        cache.get("miss")    # miss
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_delete(self):
        from core.cache import LRUCache
        cache = LRUCache()
        cache.set("k", "v", ttl=60)
        cache.delete("k")
        assert cache.get("k") is None

    def test_clear(self):
        from core.cache import LRUCache
        cache = LRUCache()
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.clear()
        assert cache.get("a") is None
        assert cache.stats["size"] == 0


class TestResponseCache:
    def test_make_key_deterministic(self):
        from core.cache import ResponseCache
        k1 = ResponseCache.make_key("crossref", "doi", "10.1234/test")
        k2 = ResponseCache.make_key("crossref", "doi", "10.1234/test")
        assert k1 == k2
        assert k1.startswith("ir:crossref:doi:")

    def test_make_key_different_inputs(self):
        from core.cache import ResponseCache
        k1 = ResponseCache.make_key("crossref", "doi", "10.1234/a")
        k2 = ResponseCache.make_key("crossref", "doi", "10.1234/b")
        assert k1 != k2

    def test_set_and_get_without_redis(self):
        from core.cache import ResponseCache
        cache = ResponseCache(redis_url="")
        cache.set("test_key", {"title": "Hello"}, ttl=60)
        assert cache.get("test_key") == {"title": "Hello"}

    def test_negative_caching(self):
        from core.cache import ResponseCache
        cache = ResponseCache()
        cache.set_not_found("missing_key")
        found, value = cache.get_or_none("missing_key")
        assert found is True
        assert value is None

    def test_not_cached_returns_false(self):
        from core.cache import ResponseCache
        cache = ResponseCache()
        found, value = cache.get_or_none("never_set")
        assert found is False

    def test_invalidate(self):
        from core.cache import ResponseCache
        cache = ResponseCache()
        cache.set("k", "v", ttl=60)
        cache.invalidate("k")
        assert cache.get("k") is None

    def test_stats(self):
        from core.cache import ResponseCache
        cache = ResponseCache()
        stats = cache.stats
        assert "size" in stats
        assert "redis_connected" in stats
        assert stats["redis_connected"] is False


# ═══════════════════════════════════════════════════════════════════════
# 2. Circuit Breaker tests
# ═══════════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def test_starts_closed(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_allows_request_when_closed(self):
        from core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test")
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=3, window_size=60)
        for _ in range(3):
            cb.allow_request()
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_rejects_when_open(self):
        from core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=100)
        cb.allow_request()
        cb.record_failure()
        cb.allow_request()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_half_open_after_recovery_timeout(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.allow_request()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_on_half_open_success(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.allow_request()
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # Transitions to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_half_open_failure(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.allow_request()
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # Transitions to HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_consecutive_failures(self):
        from core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=5)
        cb.allow_request(); cb.record_failure()
        cb.allow_request(); cb.record_failure()
        cb.allow_request(); cb.record_success()
        assert cb.stats["consecutive_failures"] == 0

    def test_stats(self):
        from core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("crossref")
        cb.allow_request(); cb.record_success()
        cb.allow_request(); cb.record_failure()
        stats = cb.stats
        assert stats["name"] == "crossref"
        assert stats["successful"] == 1
        assert stats["failed"] == 1
        assert stats["total_calls"] == 2

    def test_reset(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.allow_request(); cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_sliding_window_old_failures_expire(self):
        from core.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=3, window_size=0.05)
        cb.allow_request(); cb.record_failure()
        cb.allow_request(); cb.record_failure()
        time.sleep(0.06)  # Window expires
        cb.allow_request(); cb.record_failure()  # Only 1 in window now
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerRegistry:
    def test_get_creates_new_breaker(self):
        from core.circuit_breaker import CircuitBreakerRegistry
        registry = CircuitBreakerRegistry()
        cb = registry.get("crossref")
        assert cb.name == "crossref"

    def test_get_returns_same_breaker(self):
        from core.circuit_breaker import CircuitBreakerRegistry
        registry = CircuitBreakerRegistry()
        cb1 = registry.get("crossref")
        cb2 = registry.get("crossref")
        assert cb1 is cb2

    def test_all_stats(self):
        from core.circuit_breaker import CircuitBreakerRegistry
        registry = CircuitBreakerRegistry()
        registry.get("a")
        registry.get("b")
        assert len(registry.all_stats()) == 2

    def test_open_circuits(self):
        from core.circuit_breaker import CircuitBreakerRegistry
        registry = CircuitBreakerRegistry(failure_threshold=1)
        cb = registry.get("dead_registry")
        cb.allow_request(); cb.record_failure()
        assert "dead_registry" in registry.open_circuits()

    def test_reset_all(self):
        from core.circuit_breaker import CircuitBreakerRegistry, CircuitState
        registry = CircuitBreakerRegistry(failure_threshold=1)
        cb = registry.get("a")
        cb.allow_request(); cb.record_failure()
        registry.reset_all()
        assert cb.state == CircuitState.CLOSED


# ═══════════════════════════════════════════════════════════════════════
# 3. Parsing utilities tests
# ═══════════════════════════════════════════════════════════════════════

class TestParsing:
    def test_extract_title_str(self):
        from core.parsing import extract_title
        assert extract_title({"title": "Deep Learning"}) == "Deep Learning"

    def test_extract_title_list(self):
        from core.parsing import extract_title
        assert extract_title({"title": ["Deep Learning", "v2"]}) == "Deep Learning"

    def test_extract_title_empty_list(self):
        from core.parsing import extract_title
        assert extract_title({"title": []}) == ""

    def test_extract_title_custom_keys(self):
        from core.parsing import extract_title
        data = {"dc:title": "Test Paper"}
        assert extract_title(data, "dc:title") == "Test Paper"

    def test_extract_title_fallback_keys(self):
        from core.parsing import extract_title
        data = {"name": "Found It"}
        assert extract_title(data, "title", "name") == "Found It"

    def test_extract_authors_list_str(self):
        from core.parsing import extract_authors
        data = {"authors": ["Alice Smith", "Bob Jones"]}
        assert extract_authors(data) == ["Alice Smith", "Bob Jones"]

    def test_extract_authors_str_and(self):
        from core.parsing import extract_authors
        data = {"authors": "Alice Smith and Bob Jones"}
        assert extract_authors(data) == ["Alice Smith", "Bob Jones"]

    def test_extract_authors_str_semicolon(self):
        from core.parsing import extract_authors
        data = {"authors": "Smith, A; Jones, B"}
        assert extract_authors(data) == ["Smith, A", "Jones, B"]

    def test_extract_authors_list_dict(self):
        from core.parsing import extract_authors
        data = {"authors": [
            {"given": "Alice", "family": "Smith"},
            {"name": "Bob Jones"},
        ]}
        result = extract_authors(data)
        assert result == ["Alice Smith", "Bob Jones"]

    def test_extract_authors_display_name(self):
        from core.parsing import extract_authors
        data = {"authors": [{"display_name": "Dr. Alice Smith"}]}
        assert extract_authors(data) == ["Dr. Alice Smith"]

    def test_extract_year_int(self):
        from core.parsing import extract_year
        assert extract_year({"year": 2017}) == "2017"

    def test_extract_year_str(self):
        from core.parsing import extract_year
        assert extract_year({"year": "2017"}) == "2017"

    def test_extract_year_date_str(self):
        from core.parsing import extract_year
        assert extract_year({"date": "2017-01-15"}) == "2017"

    def test_extract_year_date_parts(self):
        from core.parsing import extract_year
        data = {"issued": {"date-parts": [[2017, 1, 15]]}}
        assert extract_year(data) == "2017"

    def test_extract_year_list(self):
        from core.parsing import extract_year
        assert extract_year({"date": ["2017-01"]}) == "2017"

    def test_extract_year_out_of_range(self):
        from core.parsing import extract_year
        assert extract_year({"year": "100"}) == ""

    def test_extract_doi_plain(self):
        from core.parsing import extract_doi
        assert extract_doi({"doi": "10.1234/test"}) == "10.1234/test"

    def test_extract_doi_url(self):
        from core.parsing import extract_doi
        data = {"doi": "https://doi.org/10.1234/test"}
        assert extract_doi(data) == "10.1234/test"

    def test_extract_doi_dx_url(self):
        from core.parsing import extract_doi
        data = {"doi": "https://dx.doi.org/10.1234/test"}
        assert extract_doi(data) == "10.1234/test"

    def test_extract_doi_invalid(self):
        from core.parsing import extract_doi
        assert extract_doi({"doi": "not-a-doi"}) == ""

    def test_extract_venue_str(self):
        from core.parsing import extract_venue
        assert extract_venue({"journal": "Nature"}) == "Nature"

    def test_extract_venue_dict(self):
        from core.parsing import extract_venue
        data = {"journal": {"name": "Nature"}}
        assert extract_venue(data) == "Nature"

    def test_extract_venue_list(self):
        from core.parsing import extract_venue
        data = {"container-title": ["Nature"]}
        assert extract_venue(data) == "Nature"

    def test_safe_get_nested(self):
        from core.parsing import safe_get
        data = {"response": {"docs": [{"title": "Found"}]}}
        assert safe_get(data, "response", "docs", "0", "title") == "Found"

    def test_safe_get_missing(self):
        from core.parsing import safe_get
        assert safe_get({}, "a", "b", default="") == ""


# ═══════════════════════════════════════════════════════════════════════
# 4. Async registry adapter tests
# ═══════════════════════════════════════════════════════════════════════

class TestAsyncRegistry:
    def test_import_async_registry(self):
        from core.async_registry import AsyncRegistryAdapter
        assert AsyncRegistryAdapter is not None

    def test_cached_response_wrapper(self):
        from core.async_registry import _CachedResponse

        async def _test():
            resp = _CachedResponse("hello world")
            assert await resp.text() == "hello world"
            assert resp.status == 200

        asyncio.run(_test())

    def test_cached_response_json(self):
        from core.async_registry import _CachedResponse

        async def _test():
            resp = _CachedResponse({"key": "value"})
            data = await resp.json()
            assert data == {"key": "value"}
            text = await resp.text()
            assert '"key"' in text

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════
# 5. Async discovery tests
# ═══════════════════════════════════════════════════════════════════════

class TestAsyncDiscovery:
    def test_import(self):
        from core.async_discovery import AsyncRegistryDiscovery
        disc = AsyncRegistryDiscovery()
        assert disc.all_adapters == []

    def test_register_sync_adapter(self):
        from core.async_discovery import AsyncRegistryDiscovery
        from core.registry import RegistryAdapter, RegistryInfo

        class DummySync(RegistryAdapter):
            def info(self):
                return RegistryInfo("dummy", "academic", "http://x",
                                    "none", 1.0, "test", ["paper"])
            def query_by_id(self, id_type, id_value):
                return None
            def search(self, title, author="", year="", **kwargs):
                return []

        disc = AsyncRegistryDiscovery()
        disc.register(DummySync())
        assert len(disc.all_adapters) == 1
        listing = disc.list_registries()
        assert listing[0]["name"] == "dummy"
        assert listing[0]["async"] is False

    def test_verify_reference_empty(self):
        from core.async_discovery import AsyncRegistryDiscovery

        async def _test():
            disc = AsyncRegistryDiscovery()
            result = await disc.verify_reference(title="Test")
            assert result["found"] is False
            assert result["matches"] == []

        asyncio.run(_test())

    def test_entity_serialization_roundtrip(self):
        from core.async_discovery import AsyncRegistryDiscovery
        from core.entity import ICEntity, EntityType

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title="Test Paper",
            authors=["Alice", "Bob"],
            year="2024",
            venue="Nature",
        )

        d = AsyncRegistryDiscovery._entity_to_dict(entity)
        assert d["title"] == "Test Paper"
        assert d["authors"] == ["Alice", "Bob"]

        restored = AsyncRegistryDiscovery._dict_to_entity(d)
        assert restored.title == "Test Paper"
        assert restored.year == "2024"


# ═══════════════════════════════════════════════════════════════════════
# 6. Batch processor tests
# ═══════════════════════════════════════════════════════════════════════

class TestBatchProcessor:
    def test_empty_batch(self):
        from core.batch_processor import BatchProcessor

        async def _test():
            proc = BatchProcessor(engine=None, concurrency=4)
            report = await proc.verify_batch([])
            assert report["total"] == 0

        asyncio.run(_test())

    def test_batch_with_mock_engine(self):
        from core.batch_processor import BatchProcessor
        from verification.engine import ReferenceResult

        class MockEngine:
            def verify_reference(self, ref, domains=None):
                return ReferenceResult(
                    key=ref.get("key", ""),
                    title=ref.get("title", ""),
                    overall="OK",
                    sources_hit=["crossref"],
                )

        async def _test():
            proc = BatchProcessor(MockEngine(), concurrency=4)
            refs = [
                {"key": "ref1", "title": "Paper A"},
                {"key": "ref2", "title": "Paper B"},
                {"key": "ref3", "title": "Paper C"},
            ]
            report = await proc.verify_batch(refs)
            assert report["total"] == 3
            assert report["ok"] == 3
            assert "crossref" in report["registries_used"]
            assert report["throughput_refs_per_sec"] > 0

        asyncio.run(_test())

    def test_batch_progress_callback(self):
        from core.batch_processor import BatchProcessor
        from verification.engine import ReferenceResult

        class MockEngine:
            def verify_reference(self, ref, domains=None):
                return ReferenceResult(
                    key=ref.get("key", ""), title=ref.get("title", ""),
                    overall="OK")

        progress_log = []

        async def on_progress(done, total, result):
            progress_log.append((done, total))

        async def _test():
            proc = BatchProcessor(MockEngine(), concurrency=2)
            refs = [{"title": f"Paper {i}"} for i in range(5)]
            await proc.verify_batch(refs, on_progress=on_progress)
            assert len(progress_log) == 5
            assert progress_log[-1][0] == 5

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════
# 7. ONNX exporter tests (import-only, no model required)
# ═══════════════════════════════════════════════════════════════════════

class TestONNXExporter:
    def test_import(self):
        from semantic.onnx_exporter import ONNXNLIVerifier, export_to_onnx
        assert ONNXNLIVerifier is not None

    def test_verifier_init(self):
        from semantic.onnx_exporter import ONNXNLIVerifier
        v = ONNXNLIVerifier(onnx_path="nonexistent.onnx")
        assert v._loaded is False

    def test_verifier_handles_missing_model(self):
        from semantic.onnx_exporter import ONNXNLIVerifier
        v = ONNXNLIVerifier(onnx_path="nonexistent.onnx")
        loaded = v._ensure_loaded()
        assert loaded is False

    def test_verifier_empty_input(self):
        from semantic.onnx_exporter import ONNXNLIVerifier
        from semantic.nli_verifier import AlignmentLabel
        v = ONNXNLIVerifier(onnx_path="nonexistent.onnx")
        result = v.verify("", "some abstract")
        assert result.label == AlignmentLabel.UNVERIFIABLE
